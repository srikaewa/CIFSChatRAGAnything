import hmac
from itsdangerous import BadSignature, URLSafeSerializer

from .settings import get_settings


def verify_admin(email: str, password: str) -> bool:
    settings = get_settings()
    return email == settings.admin_email and password == settings.admin_password


def make_session_token(email: str) -> str:
    serializer = URLSafeSerializer(get_settings().app_secret_key, salt="admin-session")
    return serializer.dumps({"email": email})


def read_session_token(token: str | None) -> str | None:
    if not token:
        return None
    serializer = URLSafeSerializer(get_settings().app_secret_key, salt="admin-session")
    try:
        data = serializer.loads(token)
    except BadSignature:
        return None
    return str(data.get("email", ""))


def make_csrf_token(email: str) -> str:
    serializer = URLSafeSerializer(get_settings().app_secret_key, salt="admin-csrf")
    return serializer.dumps({"email": email})


def verify_csrf_token(token: str, email: str) -> bool:
    if not token or not email:
        return False
    serializer = URLSafeSerializer(get_settings().app_secret_key, salt="admin-csrf")
    try:
        data = serializer.loads(token)
    except BadSignature:
        return False
    token_email = str(data.get("email", ""))
    return bool(token_email) and hmac.compare_digest(token_email, email)


def mask_secret(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 6:
        return "***"
    return f"{value[:3]}...{value[-3:]}"
