import json
import logging
import re

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.channel_config import save_channel
from chatbot_manager.channel_connections import resolve_connection_credentials
from chatbot_manager.channels.line import LineAdapter
from chatbot_manager.credentials import store_credential
from chatbot_manager.db import get_engine
from chatbot_manager.knowledge.client import KnowledgeServiceError
from chatbot_manager.models import (
    AuditEvent,
    Bot,
    BotConfigVersion,
    BotDecision,
    ChannelConnection,
    Conversation,
    ConversationMessage,
    KnowledgeService,
)
from chatbot_manager.runtime.conversations import ConversationService
from chatbot_manager.runtime.engine import BotRuntime, RuntimeRequest


def login_owner(client: TestClient) -> str:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/bots")
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert match is not None
    return match.group(1)


def serialized_audit_events(session: Session) -> str:
    return "\n".join(
        " ".join(
            (
                row.summary,
                row.before_json,
                row.after_json,
                row.request_metadata_json,
            )
        )
        for row in session.exec(select(AuditEvent)).all()
    )


def create_human_line_conversation(session: Session, secret: str) -> int:
    bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
    assert bot.id is not None
    credential = store_credential(
        session,
        "channel:line",
        {
            "channel_secret": "signature-secret",
            "channel_access_token": secret,
        },
    )
    connection = ChannelConnection(
        bot_id=bot.id,
        provider="line",
        display_name="LINE",
        webhook_key="redaction-line",
        credential_id=credential.id,
        enabled=True,
        status="ready",
    )
    session.add(connection)
    session.commit()
    session.refresh(connection)
    conversation = Conversation(
        bot_id=bot.id,
        channel_connection_id=connection.id,
        external_user_id="redaction-user",
        status="human_active",
        assigned_operator_id=0,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    session.add(
        ConversationMessage(
            conversation_id=conversation.id,
            sender_type="user",
            content="Please reply",
            external_message_id="redaction-inbound",
            metadata_json=json.dumps(
                {
                    "reply_context": {
                        "reply_token": "already-consumed",
                        "user_id": "redaction-user",
                    }
                }
            ),
        )
    )
    session.commit()
    assert conversation.id is not None
    return conversation.id


def prepare_runtime(session: Session):
    bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
    assert bot.id is not None
    config = session.get(BotConfigVersion, bot.live_config_version_id)
    assert config is not None
    service = KnowledgeService(
        name="Redaction RAG",
        service_type="lightrag",
        api_base_url="https://rag-redaction.test",
    )
    session.add(service)
    session.commit()
    session.refresh(service)
    config.knowledge_service_id = service.id
    config.fallback_reply = "Safe fallback"
    config.fallback_policy = "reply"
    session.add(config)
    session.commit()
    conversations = ConversationService(session)
    conversation = conversations.get_or_create(bot.id, 1, "redaction-runtime")
    message = conversations.record_inbound(
        conversation,
        "redaction-message",
        "Question?",
        {},
    )
    return bot, conversation, message


def test_active_connection_credentials_never_fall_back_to_legacy_channel(client) -> None:
    with Session(get_engine()) as session:
        save_channel(
            session,
            "line",
            True,
            {
                "channel_secret": "legacy-channel-secret",
                "channel_access_token": "legacy-channel-token",
            },
        )
        bot = session.exec(select(Bot).where(Bot.name == "Default Bot")).one()
        connection = ChannelConnection(
            bot_id=bot.id,
            provider="line",
            display_name="Credential-less active connection",
            webhook_key="no-credential-fallback",
            credential_id=None,
            enabled=True,
            status="ready",
        )
        session.add(connection)
        session.commit()
        session.refresh(connection)

        assert resolve_connection_credentials(session, connection) == {}


def test_channel_delivery_exception_never_persists_renders_or_logs_secret(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "channel-secret-sentinel"
    csrf = login_owner(client)
    with Session(get_engine()) as session:
        conversation_id = create_human_line_conversation(session, secret)

    async def fail_push(self, user_id: str, text: str) -> None:
        raise RuntimeError(f"provider failure containing {secret}")

    monkeypatch.setattr(LineAdapter, "send_push", fail_push)
    caplog.set_level(logging.ERROR)

    response = client.post(
        f"/conversations/{conversation_id}/reply",
        data={"csrf_token": csrf, "reply_text": "Safe operator reply"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get(f"/conversations/{conversation_id}")
    assert page.status_code == 200
    assert secret not in page.text
    assert secret not in caplog.text
    with Session(get_engine()) as session:
        message = session.exec(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .where(ConversationMessage.sender_type == "operator")
        ).one()
        assert message.delivery_status == "failed"
        assert secret not in message.metadata_json
        assert "provider_delivery_failed" in message.metadata_json
        assert secret not in serialized_audit_events(session)


@pytest.mark.asyncio
async def test_runtime_rejects_secret_bearing_knowledge_error_code(client) -> None:
    secret = "rag-runtime-secret-sentinel"

    class LeakyKnowledgeClient:
        async def query(self, request):
            raise KnowledgeServiceError(
                f"knowledge_service_unavailable:{secret}"
            )

    with Session(get_engine()) as session:
        bot, conversation, message = prepare_runtime(session)
        runtime = BotRuntime(
            session,
            knowledge_client_factory=lambda _session, _id: LeakyKnowledgeClient(),
        )
        result = await runtime.run(
            RuntimeRequest(
                bot_id=bot.id,
                conversation_id=conversation.id,
                message_id=message.id,
                text=message.content,
                provider="line",
                external_user_id="redaction-runtime",
            )
        )

        decision = session.exec(select(BotDecision)).one()
        assert result.error_code == "knowledge_service_error"
        assert decision.error_code == "knowledge_service_error"
        assert secret not in result.error_code
        assert secret not in decision.error_code


def test_knowledge_connection_transport_error_never_exposes_api_key(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "rag-key-sentinel"
    csrf = login_owner(client)
    with Session(get_engine()) as session:
        credential = store_credential(
            session,
            "knowledge_service",
            {"api_key": secret},
        )
        service = KnowledgeService(
            name="Transport Failure RAG",
            service_type="lightrag",
            api_base_url="https://rag-transport.test",
            credential_id=credential.id,
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        service_id = service.id

    async def fail_get(self, url, **kwargs):
        request = httpx.Request("GET", url)
        raise httpx.ConnectError(
            f"request failed with {secret}",
            request=request,
        )

    monkeypatch.setattr(httpx.AsyncClient, "get", fail_get)
    caplog.set_level(logging.WARNING)

    response = client.post(
        f"/knowledge-services/{service_id}/test",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert secret not in response.headers["location"]
    page = client.get(response.headers["location"])
    assert secret not in page.text
    assert secret not in caplog.text
    with Session(get_engine()) as session:
        assert secret not in serialized_audit_events(session)
