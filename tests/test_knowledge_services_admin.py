import importlib

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.credentials import read_credential
from chatbot_manager.db import get_engine
from chatbot_manager.knowledge.client import KnowledgeAnswer, KnowledgeHealth
from chatbot_manager.models import ChatEvent, KnowledgeService


def login_and_csrf(client: TestClient) -> str:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/")
    marker = 'name="csrf_token" value="'
    assert marker in page.text
    return page.text.split(marker, 1)[1].split('"', 1)[0]


def _registry_module():
    try:
        return importlib.import_module("chatbot_manager.admin.knowledge_services")
    except ModuleNotFoundError:
        pytest.fail("Knowledge Service admin registry is not implemented yet")


def _create_service(client: TestClient, csrf: str, api_key: str = "rag-secret-123") -> int:
    response = client.post(
        "/knowledge-services",
        data={
            "csrf_token": csrf,
            "name": "CIFS General RAG",
            "api_base_url": "https://rag.example.test",
            "webui_url": "https://rag.example.test/webui",
            "api_key": api_key,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    with Session(get_engine()) as session:
        service = session.exec(
            select(KnowledgeService).where(KnowledgeService.name == "CIFS General RAG")
        ).one()
        assert service.id is not None
        return service.id


def test_knowledge_service_registry_masks_api_key(client: TestClient) -> None:
    csrf = login_and_csrf(client)

    service_id = _create_service(client, csrf)

    page = client.get("/knowledge-services")
    assert page.status_code == 200
    assert "CIFS General RAG" in page.text
    assert "rag-secret-123" not in page.text
    assert "rag...123" in page.text
    assert "https://rag.example.test" in page.text
    assert 'target="_blank"' in page.text
    assert 'rel="noopener noreferrer"' in page.text

    with Session(get_engine()) as session:
        service = session.get(KnowledgeService, service_id)
        assert service is not None
        assert service.credential_id is not None
        credential = read_credential(session, service.credential_id)
        assert credential == {"api_key": "rag-secret-123"}


def test_blank_api_key_update_keeps_existing_secret(client: TestClient) -> None:
    csrf = login_and_csrf(client)
    service_id = _create_service(client, csrf, api_key="original-rag-key")

    with Session(get_engine()) as session:
        service = session.get(KnowledgeService, service_id)
        assert service is not None
        before_credential_id = service.credential_id
        before = read_credential(session, before_credential_id)

    response = client.post(
        f"/knowledge-services/{service_id}/update",
        data={
            "csrf_token": csrf,
            "name": "CIFS General RAG Updated",
            "api_base_url": "https://rag.example.test",
            "webui_url": "https://rag.example.test/webui",
            "api_key": "",
            "enabled": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        service = session.get(KnowledgeService, service_id)
        assert service is not None
        assert service.name == "CIFS General RAG Updated"
        assert service.credential_id == before_credential_id
        assert read_credential(session, service.credential_id) == before


def test_replacing_api_key_updates_existing_credential(client: TestClient) -> None:
    csrf = login_and_csrf(client)
    service_id = _create_service(client, csrf, api_key="original-rag-key")

    with Session(get_engine()) as session:
        service = session.get(KnowledgeService, service_id)
        assert service is not None
        credential_id = service.credential_id

    response = client.post(
        f"/knowledge-services/{service_id}/update",
        data={
            "csrf_token": csrf,
            "name": "CIFS General RAG",
            "api_base_url": "https://rag.example.test",
            "webui_url": "https://rag.example.test/webui",
            "api_key": "replacement-rag-key",
            "enabled": "on",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        service = session.get(KnowledgeService, service_id)
        assert service is not None
        assert service.credential_id == credential_id
        assert read_credential(session, service.credential_id) == {
            "api_key": "replacement-rag-key"
        }


def test_connection_test_persists_health_status_and_timestamp(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = login_and_csrf(client)
    service_id = _create_service(client, csrf)
    registry = _registry_module()

    class FakeClient:
        async def test_connection(self) -> KnowledgeHealth:
            return KnowledgeHealth("healthy", 25)

    monkeypatch.setattr(registry, "build_knowledge_client", lambda session, service_id: FakeClient())

    response = client.post(
        f"/knowledge-services/{service_id}/test",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        service = session.get(KnowledgeService, service_id)
        assert service is not None
        assert service.health_status == "healthy"
        assert service.last_health_check is not None


def test_connection_error_uses_stable_code_without_leaking_exception(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = login_and_csrf(client)
    service_id = _create_service(client, csrf)
    registry = _registry_module()

    def fail_build(session, service_id):
        raise RuntimeError("private upstream secret")

    monkeypatch.setattr(registry, "build_knowledge_client", fail_build)

    response = client.post(
        f"/knowledge-services/{service_id}/test",
        data={"csrf_token": csrf},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "private upstream secret" not in response.headers["location"]
    assert "knowledge_service_unavailable" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert "private upstream secret" not in page.text


def test_test_retrieval_renders_answer_and_references_without_chat_event(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    csrf = login_and_csrf(client)
    service_id = _create_service(client, csrf)
    registry = _registry_module()
    seen_questions: list[str] = []

    class FakeClient:
        async def query(self, request):
            seen_questions.append(request.question)
            return KnowledgeAnswer(
                text="Grounded answer",
                references=[{"file_name": "guide.pdf"}],
                latency_ms=12,
            )

    monkeypatch.setattr(registry, "build_knowledge_client", lambda session, service_id: FakeClient())

    response = client.post(
        f"/knowledge-services/{service_id}/test-retrieval",
        data={"csrf_token": csrf, "query": "What documents?"},
    )

    assert response.status_code == 200
    assert "Grounded answer" in response.text
    assert "guide.pdf" in response.text
    assert seen_questions == ["What documents?"]
    with Session(get_engine()) as session:
        assert session.exec(select(ChatEvent)).all() == []


def test_registry_rejects_unsafe_endpoint_scheme(client: TestClient) -> None:
    csrf = login_and_csrf(client)

    response = client.post(
        "/knowledge-services",
        data={
            "csrf_token": csrf,
            "name": "Unsafe RAG",
            "api_base_url": "file:///tmp/rag",
            "webui_url": "javascript:alert(1)",
            "api_key": "secret",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "invalid_knowledge_service_url" in response.headers["location"]
    with Session(get_engine()) as session:
        assert session.exec(select(KnowledgeService)).all() == []
