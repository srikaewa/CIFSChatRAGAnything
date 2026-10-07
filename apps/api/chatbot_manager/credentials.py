import json

from sqlmodel import Session

from .models import Credential, utc_now
from .security import decrypt_secret, encrypt_secret, mask_secret
from .settings import get_settings


def _encode_payload(payload: dict[str, str]) -> str:
    plaintext = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return encrypt_secret(plaintext, get_settings().app_encryption_key)


def _decode_payload(item: Credential) -> dict[str, str]:
    raw = decrypt_secret(item.encrypted_payload, get_settings().app_encryption_key)
    loaded = json.loads(raw)
    if not isinstance(loaded, dict):
        raise ValueError("Credential payload is invalid")
    return {str(key): str(value) for key, value in loaded.items()}


def store_credential(
    session: Session,
    credential_type: str,
    payload: dict[str, str],
) -> Credential:
    item = Credential(
        credential_type=credential_type,
        encrypted_payload=_encode_payload(payload),
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def replace_credential(
    session: Session,
    credential_id: int,
    payload: dict[str, str],
) -> Credential:
    item = session.get(Credential, credential_id)
    if item is None:
        raise LookupError("Credential not found")
    item.encrypted_payload = _encode_payload(payload)
    item.rotated_at = utc_now()
    session.add(item)
    session.commit()
    session.refresh(item)
    return item


def read_credential(session: Session, credential_id: int | None) -> dict[str, str]:
    if credential_id is None:
        return {}
    item = session.get(Credential, credential_id)
    if item is None:
        raise LookupError("Credential not found")
    payload = _decode_payload(item)
    item.last_used_at = utc_now()
    session.add(item)
    session.commit()
    return payload


def masked_credential(session: Session, credential_id: int | None) -> dict[str, str]:
    if credential_id is None:
        return {}
    item = session.get(Credential, credential_id)
    if item is None:
        raise LookupError("Credential not found")
    return {key: mask_secret(value) for key, value in _decode_payload(item).items()}
