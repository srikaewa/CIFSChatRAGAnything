from fastapi.testclient import TestClient
from sqlmodel import Session

from chatbot_manager.auth.service import password_hasher
from chatbot_manager.db import get_engine
from chatbot_manager.models import (
    Bot,
    ChannelConnection,
    Conversation,
    User,
    UserBotAccess,
)
from chatbot_manager.security import make_csrf_token


def create_user(email: str, role: str) -> int:
    with Session(get_engine()) as session:
        user = User(
            email=email,
            password_hash=password_hasher.hash("password-123"),
            role=role,
            active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.id is not None
        return user.id


def login_as(client: TestClient, *, role: str) -> int:
    email = f"{role}-user@example.local"
    user_id = create_user(email, role)
    response = client.post(
        "/login",
        data={"email": email, "password": "password-123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return user_id


def create_bot(name: str) -> int:
    with Session(get_engine()) as session:
        bot = Bot(name=name, lifecycle_status="active")
        session.add(bot)
        session.commit()
        session.refresh(bot)
        assert bot.id is not None
        return bot.id


def grant_bot_access(user_id: int, bot_id: int) -> None:
    with Session(get_engine()) as session:
        session.add(UserBotAccess(user_id=user_id, bot_id=bot_id))
        session.commit()


def create_conversation(bot_id: int, suffix: str) -> int:
    with Session(get_engine()) as session:
        connection = ChannelConnection(
            bot_id=bot_id,
            provider="line",
            display_name="LINE",
            webhook_key=f"auth-{suffix}",
            enabled=True,
            status="ready",
        )
        session.add(connection)
        session.commit()
        session.refresh(connection)
        assert connection.id is not None

        conversation = Conversation(
            bot_id=bot_id,
            channel_connection_id=connection.id,
            external_user_id=f"user-{suffix}",
            status="bot_active",
        )
        session.add(conversation)
        session.commit()
        session.refresh(conversation)
        assert conversation.id is not None
        return conversation.id


def test_owner_can_open_all_bots(client: TestClient) -> None:
    login_as(client, role="owner")
    bot_id = create_bot("Owner-visible")
    assert client.get(f"/bots/{bot_id}").status_code == 200


def test_operator_cannot_open_unassigned_bot_by_direct_url(client: TestClient) -> None:
    login_as(client, role="operator")
    bot_id = create_bot("Restricted")
    assert client.get(f"/bots/{bot_id}").status_code == 403


def test_operator_can_open_assigned_bot_and_conversation(client: TestClient) -> None:
    operator_id = login_as(client, role="operator")
    bot_id = create_bot("Assigned")
    grant_bot_access(operator_id, bot_id)
    conversation_id = create_conversation(bot_id, "assigned")
    assert client.get(f"/bots/{bot_id}").status_code == 200
    assert client.get(f"/conversations/{conversation_id}").status_code == 200


def test_operator_cannot_open_unassigned_conversation_by_direct_url(client: TestClient) -> None:
    login_as(client, role="operator")
    bot_id = create_bot("Other")
    conversation_id = create_conversation(bot_id, "other")
    assert client.get(f"/conversations/{conversation_id}").status_code == 403


def test_operator_cannot_publish_or_edit_bot_config(client: TestClient) -> None:
    operator_id = login_as(client, role="operator")
    bot_id = create_bot("Assigned mutation")
    grant_bot_access(operator_id, bot_id)
    response = client.post(
        f"/bots/{bot_id}/test/publish",
        data={"csrf_token": make_csrf_token("operator-user@example.local")},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_operator_only_sees_assigned_bots_in_lists(client: TestClient) -> None:
    operator_id = login_as(client, role="operator")
    assigned_id = create_bot("Assigned list bot")
    create_bot("Hidden list bot")
    grant_bot_access(operator_id, assigned_id)
    response = client.get("/bots")
    assert response.status_code == 200
    assert "Assigned list bot" in response.text
    assert "Hidden list bot" not in response.text


def test_operator_can_view_only_assigned_bot_analytics(client: TestClient) -> None:
    operator_id = login_as(client, role="operator")
    bot_id = create_bot("Assigned analytics")
    other_id = create_bot("Hidden analytics")
    grant_bot_access(operator_id, bot_id)
    assert client.get(f"/analytics?bot_id={bot_id}").status_code == 200
    assert client.get(f"/analytics?bot_id={other_id}").status_code == 403
    assert client.get("/analytics").status_code == 403


def test_operator_cannot_open_global_admin_surfaces(client: TestClient) -> None:
    login_as(client, role="operator")
    assert client.get("/knowledge-services").status_code == 403
    assert client.get("/incidents").status_code == 403


def test_admin_can_manage_bots_and_global_admin_surfaces(client: TestClient) -> None:
    login_as(client, role="admin")
    assert client.get("/bots").status_code == 200
    assert client.get("/knowledge-services").status_code == 200
    assert client.get("/incidents").status_code == 200
