import json

from sqlmodel import Session

from chatbot_manager.auth.authorization import CurrentUser
from chatbot_manager.models import AuditEvent


SENSITIVE_KEYS = frozenset({
    "password",
    "password_hash",
    "api_key",
    "access_token",
    "channel_access_token",
    "channel_secret",
    "app_secret",
    "bot_token",
    "webhook_secret",
    "authorization",
    "x-api-key",
})


def sanitize_audit_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if str(key).casefold() in SENSITIVE_KEYS
                else sanitize_audit_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [sanitize_audit_value(item) for item in value]
    return value


def _json(value: object) -> str:
    return json.dumps(
        sanitize_audit_value(value),
        sort_keys=True,
        default=str,
    )


def record_audit(
    session: Session,
    actor: CurrentUser | None,
    action: str,
    object_type: str,
    object_id: str,
    summary: str,
    bot_id: int | None = None,
    before: object = None,
    after: object = None,
    request_metadata: dict[str, object] | None = None,
) -> AuditEvent:
    row = AuditEvent(
        actor_user_id=actor.id if actor is not None else None,
        action=action,
        object_type=object_type,
        object_id=object_id,
        bot_id=bot_id,
        summary=summary,
        before_json=_json(before),
        after_json=_json(after),
        request_metadata_json=_json(request_metadata or {}),
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
