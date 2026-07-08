from __future__ import annotations

from pathlib import Path
from inspect import isawaitable
from typing import Any, Protocol

from chatbot_manager.settings import Settings


class RagService:
    async def answer(self, question: str, system_prompt: str) -> str:
        raise NotImplementedError("RagService.answer must be implemented by concrete service.")


class RagClientProtocol(Protocol):
    async def process_document_complete(self, file_path: str, output_dir: str, parse_method: str, **kwargs: Any) -> None: ...

    async def aquery(self, question: str, mode: str = "hybrid", system_prompt: str | None = None) -> str: ...


class FakeRagService(RagService):
    def __init__(self, answer: str = "") -> None:
        self.answer_text = answer

    async def answer(self, question: str, system_prompt: str) -> str:
        return self.answer_text


class RagAnythingService(RagService):
    def __init__(
        self,
        settings: Settings | None = None,
        rag_client: RagClientProtocol | None = None,
        llm_base_url: str | None = None,
        llm_api_key: str | None = None,
        llm_model: str | None = None,
        vision_model: str | None = None,
        embedding_model: str | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self._rag_client = rag_client
        self.llm_base_url = llm_base_url if llm_base_url is not None else self.settings.llm_base_url
        self.llm_api_key = llm_api_key if llm_api_key is not None else self.settings.llm_api_key
        self.llm_model = llm_model if llm_model is not None else self.settings.llm_default_model
        self.vision_model = vision_model if vision_model is not None else self.settings.llm_vision_model
        self.embedding_model = embedding_model if embedding_model is not None else self.settings.llm_embedding_model

    def client(self) -> RagClientProtocol:
        if self._rag_client is None:
            self._rag_client = self._build_client()
        return self._rag_client

    async def index_document(self, file_path: Path, rag_doc_id: str | None = None) -> None:
        if not self.llm_api_key.strip():
            raise RuntimeError("LLM API key is required for RAG indexing. Set it on the Assistant page or in LLM_API_KEY.")
        self.settings.rag_working_dir.mkdir(parents=True, exist_ok=True)
        client = self.client()
        await client.process_document_complete(
            file_path=str(file_path),
            output_dir=str(self.settings.rag_working_dir),
            parse_method=self.settings.rag_parse_method,
            doc_id=rag_doc_id,
            file_name=file_path.name,
        )
        await self._finalize_client(client)
        await self._raise_if_graph_available_and_empty(client)

    async def reindex_document(self, file_path: Path, rag_doc_id: str) -> None:
        if not self.llm_api_key.strip():
            raise RuntimeError("LLM API key is required for RAG indexing. Set it on the Assistant page or in LLM_API_KEY.")
        self.settings.rag_working_dir.mkdir(parents=True, exist_ok=True)
        client = self.client()
        await self._ensure_lightrag_initialized(client)
        await self._delete_stale_document_records(client, file_path.name, rag_doc_id)
        await self._finalize_client(client)
        await client.process_document_complete(
            file_path=str(file_path),
            output_dir=str(self.settings.rag_working_dir),
            parse_method=self.settings.rag_parse_method,
            doc_id=rag_doc_id,
            file_name=file_path.name,
        )
        await self._finalize_client(client)
        await self._raise_if_graph_available_and_empty(client)

    async def answer(self, question: str, system_prompt: str) -> str:
        result = await self.client().aquery(question, mode="hybrid", system_prompt=system_prompt)
        return result.strip()

    async def knowledge_graph(self, label: str, max_depth: int, max_nodes: int) -> dict[str, Any]:
        client = self.client()
        graph_getter = getattr(client, "get_knowledge_graph", None)
        if not callable(graph_getter):
            lightrag = await self._ensure_lightrag_initialized(client)
            graph_getter = getattr(lightrag, "get_knowledge_graph", None)

        if not callable(graph_getter):
            raise RuntimeError("Knowledge graph is not available. Index documents before opening the graph.")

        result = graph_getter(node_label=label, max_depth=max_depth, max_nodes=max_nodes)
        if isawaitable(result):
            result = await result
        return self._normalize_knowledge_graph(result)

    async def entity_labels(self) -> list[str]:
        client = self.client()
        labels = await self._entity_labels_from_client(client, initialize=True)
        return sorted(set(labels or []), key=str.casefold)

    async def _ensure_lightrag_initialized(self, client: Any) -> Any:
        ensure_initialized = getattr(client, "_ensure_lightrag_initialized", None)
        if callable(ensure_initialized) and getattr(client, "lightrag", None) is None:
            result = ensure_initialized()
            if isawaitable(result):
                result = await result
            if isinstance(result, dict) and result.get("success") is False:
                raise RuntimeError(str(result.get("error") or "Knowledge graph is not available."))
        return getattr(client, "lightrag", None)

    async def _delete_stale_document_records(self, client: Any, filename: str, rag_doc_id: str) -> None:
        lightrag = getattr(client, "lightrag", None)
        doc_status = getattr(lightrag, "doc_status", None)
        stale_ids = self._stale_document_ids(doc_status, filename, rag_doc_id)
        if not stale_ids:
            return

        delete_doc = getattr(lightrag, "adelete_by_doc_id", None)
        if callable(delete_doc):
            for doc_id in stale_ids:
                result = delete_doc(doc_id)
                if isawaitable(result):
                    await result

        delete_status = getattr(doc_status, "delete", None)
        if callable(delete_status):
            result = delete_status(stale_ids)
            if isawaitable(result):
                await result

    def _stale_document_ids(self, doc_status: Any, filename: str, rag_doc_id: str) -> list[str]:
        data = getattr(doc_status, "_data", {}) or {}
        stale_ids: list[str] = []
        for doc_id, record in data.items():
            record_path = str(self._graph_value(record, "file_path", ""))
            if doc_id == rag_doc_id or Path(record_path).name == filename:
                stale_ids.append(str(doc_id))
        if rag_doc_id not in stale_ids:
            stale_ids.insert(0, rag_doc_id)
        return stale_ids

    async def _finalize_client(self, client: Any) -> None:
        finalize = getattr(client, "finalize_storages", None)
        if callable(finalize):
            result = finalize()
            if isawaitable(result):
                await result

    async def _raise_if_graph_available_and_empty(self, client: Any) -> None:
        labels = await self._entity_labels_from_client(client, initialize=False)
        if labels == []:
            raise RuntimeError(
                "No graph entities were extracted. Check LLM settings, then reindex this document."
            )

    async def _entity_labels_from_client(self, client: Any, initialize: bool) -> list[str] | None:
        lightrag = await self._ensure_lightrag_initialized(client) if initialize else getattr(client, "lightrag", None)
        graph = getattr(lightrag, "chunk_entity_relation_graph", None)
        get_all_labels = getattr(graph, "get_all_labels", None)
        if not callable(get_all_labels):
            return None
        result = get_all_labels()
        if isawaitable(result):
            result = await result
        return [str(label) for label in result]

    def _normalize_knowledge_graph(self, graph: Any) -> dict[str, Any]:
        nodes = [self._normalize_graph_node(node) for node in self._graph_value(graph, "nodes", [])]
        edges = [self._normalize_graph_edge(edge) for edge in self._graph_value(graph, "edges", [])]
        return {
            "nodes": nodes,
            "edges": edges,
            "is_truncated": bool(self._graph_value(graph, "is_truncated", False)),
        }

    def _normalize_graph_node(self, node: Any) -> dict[str, Any]:
        node_id = str(self._graph_value(node, "id", ""))
        labels = self._graph_value(node, "labels", [])
        label = node_id
        if isinstance(labels, list) and labels:
            label = str(labels[0])
        return {
            "id": node_id,
            "label": label,
            "properties": self._normalize_properties(self._graph_value(node, "properties", {})),
        }

    def _normalize_graph_edge(self, edge: Any) -> dict[str, Any]:
        source = str(self._graph_value(edge, "source", ""))
        target = str(self._graph_value(edge, "target", ""))
        edge_type = self._graph_value(edge, "type", "") or ""
        edge_id = self._graph_value(edge, "id", "") or f"{source}-{target}-{edge_type}"
        return {
            "id": str(edge_id),
            "source": source,
            "target": target,
            "type": str(edge_type),
            "properties": self._normalize_properties(self._graph_value(edge, "properties", {})),
        }

    def _normalize_properties(self, properties: Any) -> dict[str, Any]:
        if isinstance(properties, dict):
            return properties
        return {}

    def _graph_value(self, item: Any, key: str, default: Any) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    def _build_client(self) -> RagClientProtocol:
        try:
            from lightrag.llm.openai import openai_complete_if_cache, openai_embed
            from lightrag.utils import EmbeddingFunc
            from raganything import RAGAnything, RAGAnythingConfig
        except ImportError as exc:
            raise RuntimeError("RAG-Anything is not installed. Run `uv sync`.") from exc

        async def llm_model_func(
            prompt: str,
            system_prompt: str | None = None,
            history_messages: list[dict[str, Any]] | None = None,
            **kwargs: Any,
        ) -> str:
            return await openai_complete_if_cache(
                self.llm_model,
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages,
                base_url=self.llm_base_url,
                api_key=self.llm_api_key,
                **kwargs,
            )

        async def vision_model_func(
            prompt: str,
            system_prompt: str | None = None,
            history_messages: list[dict[str, Any]] | None = None,
            image_inputs: list[Any] | None = None,
            **kwargs: Any,
        ) -> str:
            return await openai_complete_if_cache(
                self.vision_model,
                prompt,
                system_prompt=system_prompt,
                history_messages=history_messages,
                image_inputs=image_inputs,
                base_url=self.llm_base_url,
                api_key=self.llm_api_key,
                **kwargs,
            )

        async def embed_func(texts: list[str]) -> Any:
            return await openai_embed.func(
                texts,
                model=self.embedding_model,
                base_url=self.llm_base_url,
                api_key=self.llm_api_key,
            )

        embedding_func = EmbeddingFunc(
            embedding_dim=1536,
            max_token_size=8192,
            func=embed_func,
            model_name=self.embedding_model,
        )
        config = RAGAnythingConfig(
            working_dir=str(self.settings.rag_working_dir),
            parser=self.settings.rag_parser,
            parse_method=self.settings.rag_parse_method,
        )
        return RAGAnything(
            config=config,
            llm_model_func=llm_model_func,
            vision_model_func=vision_model_func,
            embedding_func=embedding_func,
        )
