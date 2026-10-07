import importlib
import json

import httpx
import pytest
import respx
from httpx import Response
from sqlmodel import Session

from chatbot_manager.credentials import store_credential
from chatbot_manager.db import get_engine
from chatbot_manager.models import KnowledgeService


def _client_api():
    try:
        module = importlib.import_module("chatbot_manager.knowledge.client")
    except ModuleNotFoundError:
        pytest.fail("chatbot_manager.knowledge.client is not implemented yet")
    return module


def _service_api():
    try:
        module = importlib.import_module("chatbot_manager.knowledge.service")
    except ModuleNotFoundError:
        pytest.fail("chatbot_manager.knowledge.service is not implemented yet")
    return module


@pytest.mark.asyncio
@respx.mock
async def test_lightrag_query_sends_bot_prompt_history_and_api_key() -> None:
    api = _client_api()
    route = respx.post("https://rag.example.test/query").mock(
        return_value=Response(
            200,
            json={
                "response": "Grounded answer",
                "references": [{"file_name": "guide.pdf"}],
            },
        )
    )
    client = api.LightRAGClient(
        "https://rag.example.test",
        "rag-key",
        timeout_seconds=5.0,
    )
    request = api.KnowledgeQuery(
        question="What documents?",
        user_prompt="Answer professionally in Thai.",
        conversation_history=[{"role": "user", "content": "Earlier question"}],
    )

    answer = await client.query(request)

    sent = route.calls[0].request
    assert sent.headers["x-api-key"] == "rag-key"
    payload = json.loads(sent.content)
    assert payload["query"] == "What documents?"
    assert payload["user_prompt"] == "Answer professionally in Thai."
    assert payload["conversation_history"][0]["content"] == "Earlier question"
    assert payload["include_references"] is True
    assert payload["response_type"] == "Multiple Paragraphs"
    assert answer.text == "Grounded answer"
    assert answer.references[0]["file_name"] == "guide.pdf"
    assert answer.latency_ms >= 0


@pytest.mark.asyncio
@respx.mock
async def test_health_maps_unauthorized_without_leaking_body() -> None:
    api = _client_api()
    route = respx.get("https://rag.example.test/health").mock(
        return_value=Response(401, text="secret upstream detail")
    )
    client = api.LightRAGClient("https://rag.example.test", "rag-key", timeout_seconds=5.0)

    health = await client.health()

    assert route.called
    assert health.status == "unauthorized"
    assert health.detail_code == "knowledge_service_unauthorized"
    assert "secret upstream detail" not in repr(health)


@pytest.mark.asyncio
@respx.mock
async def test_health_maps_connection_failure_to_unavailable() -> None:
    api = _client_api()
    respx.get("https://rag.example.test/health").mock(
        side_effect=httpx.ConnectError("private socket detail")
    )
    client = api.LightRAGClient("https://rag.example.test", timeout_seconds=5.0)

    health = await client.health()

    assert health.status == "unavailable"
    assert health.detail_code == "knowledge_service_unavailable"
    assert "private socket detail" not in repr(health)


@pytest.mark.asyncio
@respx.mock
async def test_health_does_not_follow_redirects() -> None:
    api = _client_api()
    redirect = respx.get("https://rag.example.test/health").mock(
        return_value=Response(307, headers={"location": "https://other.example/health"})
    )
    other = respx.get("https://other.example/health").mock(
        return_value=Response(200, json={"status": "healthy"})
    )
    client = api.LightRAGClient("https://rag.example.test", timeout_seconds=5.0)

    health = await client.health()

    assert redirect.called
    assert other.called is False
    assert health.status == "invalid"
    assert health.detail_code == "knowledge_service_http_error"


def test_lightrag_client_rejects_non_http_urls() -> None:
    api = _client_api()

    with pytest.raises(ValueError, match="invalid_knowledge_service_url"):
        api.LightRAGClient("file:///tmp/rag")
    with pytest.raises(ValueError, match="invalid_knowledge_service_url"):
        api.LightRAGClient("javascript:alert(1)")


@pytest.mark.asyncio
@respx.mock
async def test_query_rejects_malformed_success_payload_without_leaking_body() -> None:
    api = _client_api()
    respx.post("https://rag.example.test/query").mock(
        return_value=Response(200, json={"unexpected": "secret upstream payload"})
    )
    client = api.LightRAGClient("https://rag.example.test", timeout_seconds=5.0)

    with pytest.raises(api.KnowledgeServiceError) as exc_info:
        await client.query(
            api.KnowledgeQuery(
                question="What documents?",
                user_prompt="",
                conversation_history=[],
            )
        )

    assert str(exc_info.value) == "knowledge_response_invalid"
    assert "secret upstream payload" not in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_query_maps_http_error_to_stable_code_without_body() -> None:
    api = _client_api()
    respx.post("https://rag.example.test/query").mock(
        return_value=Response(500, text="internal upstream secret")
    )
    client = api.LightRAGClient("https://rag.example.test", timeout_seconds=5.0)

    with pytest.raises(api.KnowledgeServiceError) as exc_info:
        await client.query(
            api.KnowledgeQuery(
                question="What documents?",
                user_prompt="",
                conversation_history=[],
            )
        )

    assert str(exc_info.value) == "knowledge_service_unavailable"
    assert "internal upstream secret" not in str(exc_info.value)


@pytest.mark.asyncio
@respx.mock
async def test_build_knowledge_client_uses_encrypted_service_credential(client) -> None:
    api = _client_api()
    service_api = _service_api()
    with Session(get_engine()) as session:
        credential = store_credential(
            session,
            "knowledge_service",
            {"api_key": "stored-rag-key"},
        )
        service = KnowledgeService(
            name="CIFS General RAG",
            service_type="lightrag",
            api_base_url="https://rag.example.test",
            credential_id=credential.id,
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        service_id = service.id

    assert service_id is not None
    route = respx.get("https://rag.example.test/health").mock(
        return_value=Response(200, json={"status": "healthy"})
    )
    with Session(get_engine()) as session:
        built = service_api.build_knowledge_client(session, service_id)
        health = await built.health()

    assert route.calls[0].request.headers["x-api-key"] == "stored-rag-key"
    assert health.status == "healthy"


@pytest.mark.asyncio
async def test_fake_client_records_queries_and_exposes_capabilities() -> None:
    api = _client_api()
    answer = api.KnowledgeAnswer(text="fake answer", references=[], latency_ms=1)
    fake = api.FakeKnowledgeServiceClient(answer=answer)
    request = api.KnowledgeQuery(
        question="Question",
        user_prompt="Prompt",
        conversation_history=[],
    )

    returned = await fake.query(request)

    assert returned == answer
    assert fake.queries == [request]
    assert "query" in fake.capabilities()
