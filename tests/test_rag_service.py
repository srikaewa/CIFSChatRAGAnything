from pathlib import Path
from types import ModuleType

import pytest

from chatbot_manager.rag.service import RagAnythingService
from chatbot_manager.settings import Settings


class DummyRag:
    def __init__(self, answer: str = "") -> None:
        self.answer = answer
        self.process_calls: list[dict[str, object]] = []
        self.query_calls: list[dict[str, str | None]] = []
        self.graph_calls: list[dict[str, object]] = []
        self.finalize_calls = 0

    async def process_document_complete(self, file_path: str, output_dir: str, parse_method: str, **kwargs: object) -> None:
        self.process_calls.append(
            {
                "file_path": file_path,
                "output_dir": output_dir,
                "parse_method": parse_method,
                **kwargs,
            }
        )

    async def finalize_storages(self) -> None:
        self.finalize_calls += 1

    async def aquery(self, question: str, mode: str = "hybrid", system_prompt: str | None = None, **kwargs: object) -> str:
        self.query_calls.append({"question": question, "mode": mode, "system_prompt": system_prompt})
        return self.answer

    async def get_knowledge_graph(self, node_label: str, max_depth: int, max_nodes: int):
        self.graph_calls.append({"node_label": node_label, "max_depth": max_depth, "max_nodes": max_nodes})
        return {
            "nodes": [
                {"id": "product", "labels": ["Product"], "properties": {"description": "Main product"}},
                {"id": "price", "labels": ["Price"], "properties": {"amount": "100"}},
            ],
            "edges": [
                {
                    "id": "product-price",
                    "source": "product",
                    "target": "price",
                    "type": "HAS_PRICE",
                    "properties": {"weight": 1},
                }
            ],
            "is_truncated": False,
        }


@pytest.mark.asyncio
async def test_index_document_calls_rag_client_with_file_path(tmp_path: Path) -> None:
    dummy = DummyRag()
    settings = Settings(rag_working_dir=tmp_path / "rag-work", rag_parse_method="ocr")
    service = RagAnythingService(settings=settings, rag_client=dummy, llm_api_key="test-key")
    document = tmp_path / "source.pdf"

    await service.index_document(document, rag_doc_id="knowledge-7")

    assert settings.rag_working_dir.is_dir()
    assert dummy.process_calls == [
        {
            "file_path": str(document),
            "output_dir": str(settings.rag_working_dir),
            "parse_method": "ocr",
            "doc_id": "knowledge-7",
            "file_name": "source.pdf",
        }
    ]
    assert dummy.finalize_calls == 1


@pytest.mark.asyncio
async def test_index_document_requires_llm_api_key(tmp_path: Path) -> None:
    dummy = DummyRag()
    service = RagAnythingService(settings=Settings(rag_working_dir=tmp_path / "rag-work"), rag_client=dummy, llm_api_key="")

    with pytest.raises(RuntimeError, match="LLM API key is required"):
        await service.index_document(tmp_path / "source.pdf")

    assert dummy.process_calls == []


@pytest.mark.asyncio
async def test_reindex_document_deletes_stale_lightrag_records_before_processing(tmp_path: Path) -> None:
    class FakeDocStatus:
        def __init__(self) -> None:
            self._data = {
                "knowledge-7": {"file_path": "source.pdf"},
                "doc-old": {"file_path": "source.pdf"},
                "dup-old": {"file_path": "source.pdf"},
                "other": {"file_path": "other.pdf"},
            }
            self.deleted: list[list[str]] = []

        async def delete(self, ids: list[str]) -> None:
            self.deleted.append(ids)

    class FakeLightRag:
        def __init__(self) -> None:
            self.doc_status = FakeDocStatus()
            self.deleted_doc_ids: list[str] = []

        async def adelete_by_doc_id(self, doc_id: str) -> None:
            self.deleted_doc_ids.append(doc_id)

    class FakeRag(DummyRag):
        def __init__(self) -> None:
            super().__init__()
            self.lightrag = FakeLightRag()

    fake = FakeRag()
    settings = Settings(rag_working_dir=tmp_path / "rag-work")
    service = RagAnythingService(settings=settings, rag_client=fake, llm_api_key="test-key")
    document = tmp_path / "source.pdf"

    await service.reindex_document(document, rag_doc_id="knowledge-7")

    assert fake.lightrag.deleted_doc_ids == ["knowledge-7", "doc-old", "dup-old"]
    assert fake.lightrag.doc_status.deleted == [["knowledge-7", "doc-old", "dup-old"]]
    assert fake.process_calls == [
        {
            "file_path": str(document),
            "output_dir": str(settings.rag_working_dir),
            "parse_method": settings.rag_parse_method,
            "doc_id": "knowledge-7",
            "file_name": "source.pdf",
        }
    ]
    assert fake.finalize_calls == 2


@pytest.mark.asyncio
async def test_entity_labels_initializes_lightrag_and_returns_sorted_labels(tmp_path: Path) -> None:
    class FakeGraph:
        async def get_all_labels(self) -> list[str]:
            return ["zeta", "Alpha", "beta"]

    class FakeLightRag:
        chunk_entity_relation_graph = FakeGraph()

    class LazyClient:
        lightrag = None

        async def _ensure_lightrag_initialized(self):
            self.lightrag = FakeLightRag()
            return {"success": True}

    service = RagAnythingService(settings=Settings(rag_working_dir=tmp_path / "rag-work"), rag_client=LazyClient())

    assert await service.entity_labels() == ["Alpha", "beta", "zeta"]


@pytest.mark.asyncio
async def test_index_document_raises_when_no_graph_entities_are_extracted(tmp_path: Path) -> None:
    class EmptyGraph:
        async def get_all_labels(self) -> list[str]:
            return []

    class FakeLightRag:
        chunk_entity_relation_graph = EmptyGraph()

    class FakeRag(DummyRag):
        def __init__(self) -> None:
            super().__init__()
            self.lightrag = FakeLightRag()

    fake = FakeRag()
    service = RagAnythingService(settings=Settings(rag_working_dir=tmp_path / "rag-work"), rag_client=fake, llm_api_key="test-key")

    with pytest.raises(RuntimeError, match="No graph entities were extracted"):
        await service.index_document(tmp_path / "source.pdf", rag_doc_id="knowledge-7")

    assert fake.finalize_calls == 1


@pytest.mark.asyncio
async def test_answer_uses_hybrid_query_and_strips_result(tmp_path: Path) -> None:
    dummy = DummyRag(answer="  knowledge base reply\n")
    settings = Settings(rag_working_dir=tmp_path / "rag-work")
    service = RagAnythingService(settings=settings, rag_client=dummy)

    answer = await service.answer("What documents are needed?", "Use the KB.")

    assert answer == "knowledge base reply"
    assert dummy.query_calls == [
        {"question": "What documents are needed?", "mode": "hybrid", "system_prompt": "Use the KB."}
    ]


def test_build_client_passes_required_raganything_functions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class FakeConfig:
        def __init__(self, **kwargs: object) -> None:
            captured["config_kwargs"] = kwargs

    class FakeRagAnything:
        def __init__(self, **kwargs: object) -> None:
            captured["client_kwargs"] = kwargs

    fake_module = ModuleType("raganything")
    fake_module.RAGAnything = FakeRagAnything
    fake_module.RAGAnythingConfig = FakeConfig
    monkeypatch.setitem(__import__("sys").modules, "raganything", fake_module)

    settings = Settings(
        rag_working_dir=tmp_path / "rag-work",
        rag_parser="mineru",
        rag_parse_method="auto",
        llm_base_url="https://example.test/v1",
        llm_api_key="test-key",
    )

    client = RagAnythingService(
        settings=settings,
        llm_model="chat-model",
        vision_model="vision-model",
        embedding_model="embed-model",
    ).client()

    assert isinstance(client, FakeRagAnything)
    assert captured["config_kwargs"] == {
        "working_dir": str(settings.rag_working_dir),
        "parser": "mineru",
        "parse_method": "auto",
    }
    client_kwargs = captured["client_kwargs"]
    assert callable(client_kwargs["llm_model_func"])
    assert callable(client_kwargs["vision_model_func"])
    assert client_kwargs["embedding_func"].model_name == "embed-model"
    assert client_kwargs["config"] is not None


@pytest.mark.asyncio
async def test_knowledge_graph_normalizes_client_graph(tmp_path: Path) -> None:
    dummy = DummyRag()
    service = RagAnythingService(settings=Settings(rag_working_dir=tmp_path / "rag-work"), rag_client=dummy)

    graph = await service.knowledge_graph(label="Product", max_depth=2, max_nodes=50)

    assert graph == {
        "nodes": [
            {"id": "product", "label": "Product", "properties": {"description": "Main product"}},
            {"id": "price", "label": "Price", "properties": {"amount": "100"}},
        ],
        "edges": [
            {
                "id": "product-price",
                "source": "product",
                "target": "price",
                "type": "HAS_PRICE",
                "properties": {"weight": 1},
            }
        ],
        "is_truncated": False,
    }
    assert dummy.graph_calls == [{"node_label": "Product", "max_depth": 2, "max_nodes": 50}]


@pytest.mark.asyncio
async def test_knowledge_graph_all_uses_star_label(tmp_path: Path) -> None:
    dummy = DummyRag()
    service = RagAnythingService(settings=Settings(rag_working_dir=tmp_path / "rag-work"), rag_client=dummy)

    graph = await service.knowledge_graph_all(max_nodes=200)

    assert graph["nodes"][0] == {"id": "product", "label": "Product", "properties": {"description": "Main product"}}
    assert dummy.graph_calls == [{"node_label": "*", "max_depth": 0, "max_nodes": 200}]


@pytest.mark.asyncio
async def test_knowledge_graph_uses_lightrag_fallback(tmp_path: Path) -> None:
    class LightRagGraph:
        async def get_knowledge_graph(self, node_label: str, max_depth: int, max_nodes: int):
            return {"nodes": [{"id": node_label, "labels": [], "properties": {}}], "edges": [], "is_truncated": False}

    class ClientWithLightRag:
        lightrag = LightRagGraph()

    service = RagAnythingService(settings=Settings(rag_working_dir=tmp_path / "rag-work"), rag_client=ClientWithLightRag())

    graph = await service.knowledge_graph(label="Menu", max_depth=1, max_nodes=10)

    assert graph["nodes"] == [{"id": "Menu", "label": "Menu", "properties": {}}]


@pytest.mark.asyncio
async def test_knowledge_graph_initializes_lightrag_before_fallback(tmp_path: Path) -> None:
    class LightRagGraph:
        async def get_knowledge_graph(self, node_label: str, max_depth: int, max_nodes: int):
            return {"nodes": [{"id": node_label, "labels": ["Topic"], "properties": {}}], "edges": [], "is_truncated": False}

    class LazyLightRagClient:
        lightrag = None

        async def _ensure_lightrag_initialized(self):
            self.lightrag = LightRagGraph()
            return {"success": True}

    service = RagAnythingService(settings=Settings(rag_working_dir=tmp_path / "rag-work"), rag_client=LazyLightRagClient())

    graph = await service.knowledge_graph(label="DNA", max_depth=1, max_nodes=10)

    assert graph["nodes"] == [{"id": "DNA", "label": "Topic", "properties": {}}]


@pytest.mark.asyncio
async def test_knowledge_graph_unavailable_raises_clear_error(tmp_path: Path) -> None:
    class ClientWithoutGraph:
        pass

    service = RagAnythingService(settings=Settings(rag_working_dir=tmp_path / "rag-work"), rag_client=ClientWithoutGraph())

    with pytest.raises(RuntimeError, match="Knowledge graph is not available"):
        await service.knowledge_graph(label="Menu", max_depth=1, max_nodes=10)
