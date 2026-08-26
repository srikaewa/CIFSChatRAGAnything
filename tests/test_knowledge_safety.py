from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from chatbot_manager.db import get_engine
from chatbot_manager.models import KnowledgeDocument
from chatbot_manager.settings import get_settings


def login(client: TestClient) -> str:
    response = client.post(
        "/login",
        data={"email": "admin@example.local", "password": "admin1234!"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    page = client.get("/")
    marker = 'name="csrf_token" value="'
    return page.text.split(marker, 1)[1].split('"', 1)[0]


def test_oversize_upload_is_rejected_without_partial_state(client: TestClient, tmp_path: Path) -> None:
    settings = get_settings()
    settings.upload_dir = tmp_path
    settings.max_upload_bytes = 5
    csrf_token = login(client)

    response = client.post(
        "/knowledge",
        data={"csrf_token": csrf_token},
        files=[("files", ("large.txt", b"123456", "text/plain"))],
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/knowledge?error=file_too_large"
    assert list(tmp_path.iterdir()) == []
    with Session(get_engine()) as session:
        assert session.exec(select(KnowledgeDocument)).all() == []


def test_duplicate_filenames_use_isolated_storage_paths(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    async def fake_index(document_id: int, path: str) -> None:
        return None

    monkeypatch.setattr("chatbot_manager.admin.routes.index_document_task", fake_index)
    settings = get_settings()
    settings.upload_dir = tmp_path
    csrf_token = login(client)

    response = client.post(
        "/knowledge",
        data={"csrf_token": csrf_token},
        files=[
            ("files", ("menu.txt", b"first", "text/plain")),
            ("files", ("menu.txt", b"second", "text/plain")),
        ],
        follow_redirects=False,
    )

    assert response.status_code == 303
    with Session(get_engine()) as session:
        documents = session.exec(select(KnowledgeDocument).order_by(KnowledgeDocument.id)).all()
        assert [document.filename for document in documents] == ["menu.txt", "menu.txt"]
        paths = [Path(document.path) for document in documents]
        assert paths[0] != paths[1]
        assert paths[0].suffix == ".txt"
        assert paths[1].suffix == ".txt"
        assert paths[0].read_bytes() == b"first"
        assert paths[1].read_bytes() == b"second"


def test_unsupported_upload_redirects_with_feedback(client: TestClient, tmp_path: Path) -> None:
    settings = get_settings()
    settings.upload_dir = tmp_path
    csrf_token = login(client)

    response = client.post(
        "/knowledge",
        data={"csrf_token": csrf_token},
        files=[("files", ("script.exe", b"bad", "application/octet-stream"))],
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/knowledge?error=unsupported_file_type"
    page = client.get(response.headers["location"])
    assert "Unsupported file type" in page.text


def test_rag_delete_failure_preserves_document_and_source(client: TestClient, tmp_path: Path, monkeypatch) -> None:
    class FailingRagService:
        async def delete_document(self, rag_doc_id: str) -> None:
            raise RuntimeError("backend internal detail")

    source = tmp_path / "stored.txt"
    source.write_bytes(b"recoverable")
    with Session(get_engine()) as session:
        document = KnowledgeDocument(
            filename="menu.txt",
            path=str(source),
            rag_doc_id="knowledge-1",
            status="indexed",
        )
        session.add(document)
        session.commit()

    monkeypatch.setattr("chatbot_manager.admin.routes.rag_service_from_assistant", lambda settings: FailingRagService())
    csrf_token = login(client)
    response = client.post(
        "/knowledge/1/delete",
        data={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/knowledge?error=delete_failed"
    assert source.read_bytes() == b"recoverable"
    with Session(get_engine()) as session:
        document = session.get(KnowledgeDocument, 1)
        assert document is not None
        assert document.status == "failed"
        assert document.error == "Could not remove the document from the knowledge index. Retry deletion."
        assert "internal detail" not in document.error
