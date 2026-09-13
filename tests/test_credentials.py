import importlib

import pytest
from sqlmodel import Session

from chatbot_manager import models
from chatbot_manager.db import get_engine


def _credential_api():
    try:
        module = importlib.import_module("chatbot_manager.credentials")
    except ModuleNotFoundError:
        pytest.fail("chatbot_manager.credentials is not implemented yet")

    credential_model = getattr(models, "Credential", None)
    service_model = getattr(models, "KnowledgeService", None)
    assert credential_model is not None, "Credential model is not implemented yet"
    assert service_model is not None, "KnowledgeService model is not implemented yet"
    return (
        service_model,
        module.masked_credential,
        module.read_credential,
        module.replace_credential,
        module.store_credential,
    )


def test_credential_payload_is_encrypted_and_replace_only(client) -> None:
    _, masked_credential, read_credential, replace_credential, store_credential = _credential_api()

    with Session(get_engine()) as session:
        credential = store_credential(
            session,
            "knowledge_service",
            {"api_key": "rag-secret-123"},
        )
        credential_id = credential.id
        assert credential_id is not None
        encrypted = credential.encrypted_payload
        assert "rag-secret-123" not in encrypted
        assert encrypted.startswith("enc:v1:")
        assert read_credential(session, credential_id) == {"api_key": "rag-secret-123"}
        assert masked_credential(session, credential_id)["api_key"] == "rag...123"

        replaced = replace_credential(
            session,
            credential_id,
            {"api_key": "new-secret-456"},
        )
        assert replaced.id == credential_id
        assert read_credential(session, credential_id) == {"api_key": "new-secret-456"}
        assert replaced.rotated_at is not None


def test_knowledge_service_references_credential(client) -> None:
    KnowledgeService, _, _, _, store_credential = _credential_api()

    with Session(get_engine()) as session:
        credential = store_credential(
            session,
            "knowledge_service",
            {"api_key": "secret"},
        )
        assert credential.id is not None
        service = KnowledgeService(
            name="CIFS General RAG",
            service_type="lightrag",
            api_base_url="https://rag.example.test",
            webui_url="https://rag.example.test/webui",
            credential_id=credential.id,
        )
        session.add(service)
        session.commit()
        session.refresh(service)

        assert service.credential_id == credential.id
        assert service.enabled is True
        assert service.health_status == "unknown"
