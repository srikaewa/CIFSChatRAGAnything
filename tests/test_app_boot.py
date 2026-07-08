from fastapi.testclient import TestClient
from sqlalchemy import text


def test_health_endpoint(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_database_url_override_is_applied_after_import(monkeypatch) -> None:
    from chatbot_manager import db
    from chatbot_manager.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    db.reset_engine()

    assert str(db.get_engine().url) == "sqlite://"


def test_in_memory_sqlite_keeps_tables_across_connections(monkeypatch) -> None:
    from chatbot_manager import db
    from chatbot_manager.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    db.reset_engine()

    engine = db.get_engine()
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE smoke (id INTEGER PRIMARY KEY)"))

    with engine.connect() as connection:
        result = connection.execute(text("SELECT COUNT(*) FROM smoke"))

    assert result.scalar_one() == 0


def test_sqlite_migration_adds_rag_doc_id_to_existing_knowledge_table(monkeypatch) -> None:
    from chatbot_manager import db
    from chatbot_manager.settings import get_settings

    monkeypatch.setenv("DATABASE_URL", "sqlite://")
    get_settings.cache_clear()
    db.reset_engine()

    engine = db.get_engine()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE knowledgedocument (
                    id INTEGER PRIMARY KEY,
                    filename VARCHAR NOT NULL,
                    path VARCHAR NOT NULL,
                    status VARCHAR NOT NULL,
                    error VARCHAR NOT NULL,
                    indexed_at DATETIME,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )
                """
            )
        )

    db.init_db()

    with engine.connect() as connection:
        columns = {row[1] for row in connection.execute(text("PRAGMA table_info(knowledgedocument)")).all()}

    assert "rag_doc_id" in columns
