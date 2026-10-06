import re

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.auth.service import authenticate_user, password_hasher
from chatbot_manager.db import get_engine
from chatbot_manager.models import Bot, User, UserBotAccess


def login_owner(client: TestClient) -> int:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(get_engine()) as session:
        owner = session.exec(
            select(User).where(User.email == "admin@example.local")
        ).one()
        assert owner.id is not None
        return owner.id


def csrf(client: TestClient) -> str:
    response = client.get("/bots")
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match is not None
    return match.group(1)


def create_user(email: str, role: str, *, active: bool = True) -> int:
    with Session(get_engine()) as session:
        user = User(
            email=email,
            password_hash=password_hasher.hash("password-123"),
            role=role,
            active=active,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.id is not None
        return user.id


def create_bot(name: str) -> int:
    with Session(get_engine()) as session:
        bot = Bot(name=name, lifecycle_status="active")
        session.add(bot)
        session.commit()
        session.refresh(bot)
        assert bot.id is not None
        return bot.id


def assigned_bot_ids(user_id: int) -> set[int]:
    with Session(get_engine()) as session:
        return set(
            session.exec(
                select(UserBotAccess.bot_id).where(UserBotAccess.user_id == user_id)
            ).all()
        )


def test_owner_creates_operator_with_hashed_password(client: TestClient) -> None:
    login_owner(client)
    response = client.post(
        "/users",
        data={
            "csrf_token": csrf(client),
            "email": "NEW.OPERATOR@example.local",
            "password": "strong-password-123",
            "role": "operator",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    with Session(get_engine()) as session:
        user = session.exec(
            select(User).where(User.email == "new.operator@example.local")
        ).one()
        assert user.password_hash != "strong-password-123"
        assert user.role == "operator"
        assert user.active is True
        assert authenticate_user(
            session,
            "NEW.OPERATOR@example.local",
            "strong-password-123",
        ) is not None


def test_owner_rejects_duplicate_user_email(client: TestClient) -> None:
    login_owner(client)
    create_user("duplicate@example.local", "operator")

    response = client.post(
        "/users",
        data={
            "csrf_token": csrf(client),
            "email": "DUPLICATE@example.local",
            "password": "another-password",
            "role": "operator",
        },
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_owner_assigns_operator_to_selected_bots(client: TestClient) -> None:
    login_owner(client)
    operator_id = create_user("assigned@example.local", "operator")
    bot_a = create_bot("A")
    bot_b = create_bot("B")

    response = client.post(
        f"/users/{operator_id}/bots",
        data={
            "csrf_token": csrf(client),
            "bot_ids": [str(bot_a), str(bot_b)],
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert assigned_bot_ids(operator_id) == {bot_a, bot_b}


def test_owner_replaces_assignments_and_role_change_clears_stale_access(
    client: TestClient,
) -> None:
    login_owner(client)
    operator_id = create_user("replace@example.local", "operator")
    bot_a = create_bot("Assigned A")
    bot_b = create_bot("Assigned B")
    with Session(get_engine()) as session:
        session.add(UserBotAccess(user_id=operator_id, bot_id=bot_a))
        session.commit()

    response = client.post(
        f"/users/{operator_id}/bots",
        data={"csrf_token": csrf(client), "bot_ids": [str(bot_b)]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert assigned_bot_ids(operator_id) == {bot_b}

    response = client.post(
        f"/users/{operator_id}/update",
        data={"csrf_token": csrf(client), "role": "admin", "active": "on"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert assigned_bot_ids(operator_id) == set()


def test_owner_resets_password_without_rendering_plaintext(client: TestClient) -> None:
    login_owner(client)
    user_id = create_user("reset@example.local", "operator")

    response = client.post(
        f"/users/{user_id}/password",
        data={"csrf_token": csrf(client), "password": "new-secret-456"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert "new-secret-456" not in response.text

    with Session(get_engine()) as session:
        user = session.get(User, user_id)
        assert user is not None
        assert user.password_hash != "new-secret-456"
        assert authenticate_user(session, user.email, "new-secret-456") is not None


def test_owner_cannot_deactivate_last_active_owner(client: TestClient) -> None:
    owner_id = login_owner(client)

    response = client.post(
        f"/users/{owner_id}/update",
        data={"csrf_token": csrf(client), "role": "owner", "active": ""},
        follow_redirects=False,
    )

    assert response.status_code == 400


def test_owner_can_update_owner_when_another_active_owner_exists(
    client: TestClient,
) -> None:
    owner_id = login_owner(client)
    create_user("backup-owner@example.local", "owner")

    response = client.post(
        f"/users/{owner_id}/update",
        data={"csrf_token": csrf(client), "role": "admin", "active": "on"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_users_page_never_renders_password_hashes(client: TestClient) -> None:
    login_owner(client)
    user_id = create_user("visible@example.local", "operator")
    bot_id = create_bot("Visible Bot")
    with Session(get_engine()) as session:
        user = session.get(User, user_id)
        assert user is not None
        password_hash = user.password_hash
        session.add(UserBotAccess(user_id=user_id, bot_id=bot_id))
        session.commit()

    response = client.get("/users")

    assert response.status_code == 200
    assert "visible@example.local" in response.text
    assert "Visible Bot" in response.text
    assert password_hash not in response.text


def test_users_route_and_navigation_are_owner_only(client: TestClient) -> None:
    login_owner(client)
    owner_page = client.get("/bots")
    assert owner_page.status_code == 200
    assert 'href="/users"' in owner_page.text
    assert client.get("/users").status_code == 200

    client.post(
        "/logout",
        data={"csrf_token": csrf(client)},
        follow_redirects=False,
    )
    create_user("admin-user@example.local", "admin")
    login = client.post(
        "/login",
        data={"email": "admin-user@example.local", "password": "password-123"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    admin_page = client.get("/bots")
    assert admin_page.status_code == 200
    assert 'href="/users"' not in admin_page.text
    assert client.get("/users").status_code == 403


def test_operator_cannot_open_users_or_see_users_navigation(
    client: TestClient,
) -> None:
    create_user("operator-user@example.local", "operator")
    login = client.post(
        "/login",
        data={"email": "operator-user@example.local", "password": "password-123"},
        follow_redirects=False,
    )
    assert login.status_code == 303

    bots_page = client.get("/bots")
    assert bots_page.status_code == 200
    assert 'href="/users"' not in bots_page.text
    assert client.get("/users").status_code == 403
