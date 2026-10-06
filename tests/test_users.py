import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from chatbot_manager.models import Bot, User, UserBotAccess


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
