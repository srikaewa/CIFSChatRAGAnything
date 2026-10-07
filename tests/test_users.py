import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from chatbot_manager.auth.service import (
    authenticate_user,
    bootstrap_owner,
    password_hasher,
)
from chatbot_manager.db import get_engine
from chatbot_manager.models import Bot, User, UserBotAccess
from chatbot_manager.settings import get_settings


def memory_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def test_user_and_bot_access_round_trip() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        bot = Bot(name="Admission", lifecycle_status="active")
        user = User(
            email="operator@example.local",
            password_hash="hash",
            role="operator",
            active=True,
        )
        session.add(bot)
        session.add(user)
        session.commit()
        session.refresh(bot)
        session.refresh(user)
        assert bot.id is not None
        assert user.id is not None

        session.add(UserBotAccess(user_id=user.id, bot_id=bot.id))
        session.commit()
        access = session.exec(select(UserBotAccess)).one()

        assert access.user_id == user.id
        assert access.bot_id == bot.id
        assert user.role == "operator"
        assert user.active is True


def test_user_email_is_unique() -> None:
    engine = memory_engine()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(
            User(
                email="owner@example.local",
                password_hash="hash-one",
                role="owner",
            )
        )
        session.commit()
        session.add(
            User(
                email="owner@example.local",
                password_hash="hash-two",
                role="admin",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_bootstrap_owner_hashes_current_admin_password_once(client) -> None:
    with Session(get_engine()) as session:
        first = bootstrap_owner(session)
        second = bootstrap_owner(session)

        assert first.id == second.id
        assert first.email == get_settings().admin_email
        assert first.role == "owner"
        assert first.active is True
        assert first.password_hash != get_settings().admin_password
        authenticated = authenticate_user(
            session,
            first.email.upper(),
            get_settings().admin_password,
        )
        assert authenticated is not None
        assert authenticated.id == first.id


def test_inactive_or_wrong_password_user_cannot_authenticate(client) -> None:
    with Session(get_engine()) as session:
        owner = bootstrap_owner(session)
        assert authenticate_user(session, owner.email, "wrong-password") is None
        owner.active = False
        session.add(owner)
        session.commit()
        assert authenticate_user(
            session,
            owner.email,
            get_settings().admin_password,
        ) is None


def test_login_uses_persisted_database_user(client) -> None:
    with Session(get_engine()) as session:
        user = User(
            email="admin2@example.local",
            password_hash=password_hasher.hash("database-password"),
            role="admin",
            active=True,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        assert user.id is not None

    response = client.post(
        "/login",
        data={
            "email": "ADMIN2@example.local",
            "password": "database-password",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
