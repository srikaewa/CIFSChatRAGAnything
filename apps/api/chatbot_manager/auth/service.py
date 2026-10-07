from pwdlib import PasswordHash
from sqlmodel import Session, select

from chatbot_manager.models import User
from chatbot_manager.settings import get_settings


password_hasher = PasswordHash.recommended()


def bootstrap_owner(session: Session) -> User:
    existing = session.exec(select(User).order_by(User.id)).first()
    if existing is not None:
        return existing

    settings = get_settings()
    owner = User(
        email=settings.admin_email.strip().lower(),
        password_hash=password_hasher.hash(settings.admin_password),
        role="owner",
        active=True,
    )
    session.add(owner)
    session.commit()
    session.refresh(owner)
    return owner


def authenticate_user(session: Session, email: str, password: str) -> User | None:
    normalized_email = email.strip().lower()
    user = session.exec(select(User).where(User.email == normalized_email)).first()
    if user is None or not user.active:
        return None
    if not password_hasher.verify(password, user.password_hash):
        return None
    return user


def load_active_user(session: Session, user_id: int) -> User | None:
    user = session.get(User, user_id)
    if user is None or not user.active:
        return None
    return user
