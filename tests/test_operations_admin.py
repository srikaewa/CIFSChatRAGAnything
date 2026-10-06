import json
from datetime import datetime, timedelta

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.db import get_engine
from chatbot_manager.models import (
    Bot,
    BotDecision,
    ChannelConnection,
    Conversation,
    ConversationMessage,
    Incident,
    KnowledgeService,
)


DAY_START = datetime(2026, 10, 6, 0, 0, 0)
DAY_END = datetime(2026, 10, 6, 23, 59, 59)


def login(client: TestClient) -> None:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303


def csrf_from_page(html: str) -> str:
    marker = 'name="csrf_token" value="'
    assert marker in html
    return html.split(marker, 1)[1].split('"', 1)[0]


def add_bot(session: Session, name: str, *, status: str = "active") -> Bot:
    bot = Bot(name=name, lifecycle_status=status)
    session.add(bot)
    session.commit()
    session.refresh(bot)
    assert bot.id is not None
    return bot


def add_incident(
    session: Session,
    *,
    severity: str,
    source_type: str,
    source_id: str,
    incident_type: str,
    affected_bot_ids: tuple[int, ...] = (),
    status: str = "open",
) -> Incident:
    row = Incident(
        severity=severity,
        source_type=source_type,
        source_id=source_id,
        incident_type=incident_type,
        deduplication_key=f"{incident_type}:{source_type}_{source_id}",
        status=status,
        first_seen_at=DAY_START + timedelta(hours=8),
        last_seen_at=DAY_START + timedelta(hours=9),
        affected_bot_ids_json=json.dumps(list(affected_bot_ids)),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.id is not None
    return row


def test_command_center_prioritizes_needs_attention_and_links_to_source(
    client: TestClient,
) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = add_bot(session, "Admissions Bot")
        critical = add_incident(
            session,
            severity="critical",
            source_type="channel",
            source_id="7",
            incident_type="channel_not_ready",
            affected_bot_ids=(bot.id,),
        )
        warning = add_incident(
            session,
            severity="warning",
            source_type="system",
            source_id="human_queue",
            incident_type="human_wait_sla",
            affected_bot_ids=(bot.id,),
        )
        bot_id = bot.id
        critical_id = critical.id
        warning_id = warning.id

    response = client.get("/")

    assert response.status_code == 200
    assert "<h1>Command Center</h1>" in response.text
    assert response.text.index("Critical") < response.text.index("Warning")
    assert f'href="/incidents/{critical_id}"' in response.text
    assert f'href="/bots/{bot_id}/channels"' in response.text
    assert f'href="/incidents/{warning_id}"' in response.text
    assert 'href="/conversations?status=needs_human"' in response.text


def test_shared_rag_incident_lists_affected_bots_once(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bots = [add_bot(session, name) for name in ("Admissions Bot", "Scholarship Bot", "FAQ Bot")]
        row = add_incident(
            session,
            severity="critical",
            source_type="knowledge_service",
            source_id="3",
            incident_type="knowledge_service_unavailable",
            affected_bot_ids=tuple(bot.id for bot in bots),
        )
        incident_id = row.id
        bot_names = [bot.name for bot in bots]

    response = client.get("/")

    assert response.status_code == 200
    assert response.text.count(f'data-incident-id="{incident_id}"') == 1
    for bot_name in bot_names:
        assert bot_name in response.text


def test_fallback_metric_links_to_filtered_conversations(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = add_bot(session, "Fallback Bot")
        session.add(
            BotDecision(
                message_id=101,
                bot_id=bot.id,
                config_version_id=1,
                decision_type="fallback",
                total_latency_ms=250,
                created_at=DAY_START + timedelta(hours=10),
            )
        )
        session.commit()
        bot_id = bot.id

    response = client.get(
        "/analytics",
        params={
            "bot_id": bot_id,
            "from": DAY_START.isoformat(),
            "to": DAY_END.isoformat(),
        },
    )

    assert response.status_code == 200
    assert "Quality" in response.text
    assert "Fallback rate" in response.text
    assert f"bot_id={bot_id}" in response.text
    assert "decision=fallback" in response.text
    assert f"from={DAY_START.isoformat()}" in response.text
    assert f"to={DAY_END.isoformat()}" in response.text


def test_system_status_is_critical_when_one_active_bot_has_critical_dependency(
    client: TestClient,
) -> None:
    login(client)
    with Session(get_engine()) as session:
        bots = [add_bot(session, f"Fleet Bot {index}") for index in range(6)]
        add_incident(
            session,
            severity="critical",
            source_type="channel",
            source_id="91",
            incident_type="channel_not_ready",
            affected_bot_ids=(bots[-1].id,),
        )

    response = client.get("/")

    assert response.status_code == 200
    assert 'data-system-status="Critical"' in response.text
    assert "System status" in response.text


def test_system_status_is_degraded_when_active_channel_health_is_unverified(
    client: TestClient,
) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = add_bot(session, "Unverified Channel Bot")
        session.add(
            ChannelConnection(
                bot_id=bot.id,
                provider="line",
                display_name="LINE",
                webhook_key="unverified-channel-line",
                enabled=True,
                status="ready",
            )
        )
        session.commit()

    response = client.get("/")

    assert response.status_code == 200
    assert 'data-system-status="Degraded"' in response.text
    assert 'data-system-status="Healthy"' not in response.text


def test_bot_overview_shows_health_summary_without_full_bi_charts(
    client: TestClient,
) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = add_bot(session, "Compact Health Bot")
        service = KnowledgeService(
            name="Primary RAG",
            api_base_url="https://rag.example.test",
            health_status="healthy",
        )
        session.add(service)
        session.commit()
        session.refresh(service)
        session.add(
            ChannelConnection(
                bot_id=bot.id,
                provider="line",
                display_name="LINE",
                webhook_key="compact-health-line",
                enabled=True,
                status="ready",
            )
        )
        session.add(
            Conversation(
                bot_id=bot.id,
                channel_connection_id=1,
                external_user_id="today-user",
                status="bot_active",
                started_at=DAY_START + timedelta(hours=11),
                last_message_at=DAY_START + timedelta(hours=11),
            )
        )
        session.commit()
        bot_id = bot.id

    response = client.get(f"/bots/{bot_id}")

    assert response.status_code == 200
    assert "Operational health" in response.text
    assert "Channel evidence" in response.text
    assert "Activity today" in response.text
    assert f'href="/analytics?bot_id={bot_id}"' in response.text
    assert "global-analytics-chart" not in response.text


def test_incidents_filter_detail_and_acknowledgement(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = add_bot(session, "Incident Bot")
        target = add_incident(
            session,
            severity="critical",
            source_type="channel",
            source_id="11",
            incident_type="channel_not_ready",
            affected_bot_ids=(bot.id,),
        )
        add_incident(
            session,
            severity="warning",
            source_type="system",
            source_id="human_queue",
            incident_type="human_wait_sla",
        )
        target_id = target.id

    listing = client.get("/incidents?severity=critical&status=open&source=channel")
    assert listing.status_code == 200
    assert "channel_not_ready" in listing.text
    assert "human_wait_sla" not in listing.text
    assert f'href="/incidents/{target_id}"' in listing.text

    detail = client.get(f"/incidents/{target_id}")
    assert detail.status_code == 200
    assert "Incident timeline" in detail.text
    assert "Incident Bot" in detail.text
    assert "First seen" in detail.text
    assert "Last seen" in detail.text
    assert "Acknowledge" in detail.text

    csrf = csrf_from_page(detail.text)
    response = client.post(
        f"/incidents/{target_id}/acknowledge",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(get_engine()) as session:
        stored = session.get(Incident, target_id)
        assert stored is not None
        assert stored.status == "acknowledged"


def test_metric_drilldown_filters_conversation_results(client: TestClient) -> None:
    login(client)
    with Session(get_engine()) as session:
        bot = add_bot(session, "Drilldown Bot")
        connection = ChannelConnection(
            bot_id=bot.id,
            provider="line",
            display_name="LINE",
            webhook_key="drilldown-line",
            enabled=True,
            status="ready",
        )
        session.add(connection)
        session.commit()
        session.refresh(connection)
        assert connection.id is not None

        fallback_conversation = Conversation(
            bot_id=bot.id,
            channel_connection_id=connection.id,
            external_user_id="fallback-user",
            status="bot_active",
            started_at=DAY_START + timedelta(hours=12),
            last_message_at=DAY_START + timedelta(hours=12),
        )
        rule_conversation = Conversation(
            bot_id=bot.id,
            channel_connection_id=connection.id,
            external_user_id="rule-user",
            status="bot_active",
            started_at=DAY_START + timedelta(hours=13),
            last_message_at=DAY_START + timedelta(hours=13),
        )
        session.add_all([fallback_conversation, rule_conversation])
        session.commit()
        session.refresh(fallback_conversation)
        session.refresh(rule_conversation)
        messages = [
            ConversationMessage(
                conversation_id=fallback_conversation.id,
                sender_type="user",
                content="fallback question",
                created_at=DAY_START + timedelta(hours=12),
            ),
            ConversationMessage(
                conversation_id=rule_conversation.id,
                sender_type="user",
                content="rule question",
                created_at=DAY_START + timedelta(hours=13),
            ),
        ]
        session.add_all(messages)
        session.commit()
        for message in messages:
            session.refresh(message)
            assert message.id is not None
        session.add_all(
            [
                BotDecision(
                    message_id=messages[0].id,
                    bot_id=bot.id,
                    config_version_id=1,
                    decision_type="fallback",
                    total_latency_ms=200,
                    created_at=DAY_START + timedelta(hours=12),
                ),
                BotDecision(
                    message_id=messages[1].id,
                    bot_id=bot.id,
                    config_version_id=1,
                    decision_type="rule",
                    total_latency_ms=100,
                    created_at=DAY_START + timedelta(hours=13),
                ),
            ]
        )
        session.commit()
        bot_id = bot.id

    response = client.get(
        "/conversations",
        params={
            "bot_id": bot_id,
            "decision": "fallback",
            "from": DAY_START.isoformat(),
            "to": DAY_END.isoformat(),
        },
    )

    assert response.status_code == 200
    assert "fallback-user" in response.text
    assert "rule-user" not in response.text


def test_primary_navigation_matches_phase_five_information_architecture(
    client: TestClient,
) -> None:
    login(client)

    response = client.get("/")

    assert response.status_code == 200
    labels = [
        "Command Center",
        "Bots",
        "Conversations",
        "Knowledge Services",
        "Analytics",
        "Incidents",
    ]
    positions = [response.text.index(f">{label}<") for label in labels]
    assert positions == sorted(positions)
    assert 'href="/analytics"' in response.text
    assert 'href="/incidents"' in response.text
    assert ">Dashboard<" not in response.text
