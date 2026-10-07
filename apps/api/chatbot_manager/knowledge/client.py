from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Protocol
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class KnowledgeQuery:
    question: str
    user_prompt: str
    conversation_history: list[dict[str, str]]
    include_references: bool = True
    response_type: str = "Multiple Paragraphs"


@dataclass(frozen=True)
class KnowledgeAnswer:
    text: str
    references: list[dict[str, object]]
    latency_ms: int


@dataclass(frozen=True)
class KnowledgeHealth:
    status: str
    latency_ms: int | None
    detail_code: str = ""


SAFE_KNOWLEDGE_ERROR_CODES = frozenset(
    {
        "knowledge_service_error",
        "knowledge_service_unavailable",
        "knowledge_service_unauthorized",
        "knowledge_service_http_error",
        "knowledge_response_invalid",
    }
)


class KnowledgeServiceError(RuntimeError):
    """Stable external-knowledge error that never embeds raw upstream payloads."""

    def __init__(self, code: str) -> None:
        self.code = (
            code
            if code in SAFE_KNOWLEDGE_ERROR_CODES
            else "knowledge_service_error"
        )
        super().__init__(self.code)


class KnowledgeServiceClient(Protocol):
    async def health(self) -> KnowledgeHealth:
        raise NotImplementedError

    async def query(self, request: KnowledgeQuery) -> KnowledgeAnswer:
        raise NotImplementedError

    async def test_connection(self) -> KnowledgeHealth:
        raise NotImplementedError

    def capabilities(self) -> frozenset[str]:
        raise NotImplementedError


def _latency_ms(started: float) -> int:
    return int((perf_counter() - started) * 1000)


def _query_error_code(response: httpx.Response) -> str:
    if response.status_code in {401, 403}:
        return "knowledge_service_unauthorized"
    if response.status_code == 429 or response.status_code >= 500:
        return "knowledge_service_unavailable"
    return "knowledge_service_http_error"


class LightRAGClient:
    def __init__(
        self,
        api_base_url: str,
        api_key: str = "",
        timeout_seconds: float = 10.0,
    ) -> None:
        base = api_base_url.rstrip("/")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("invalid_knowledge_service_url")
        self._base = base
        self._api_key = api_key
        self._timeout = httpx.Timeout(timeout_seconds)

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": self._api_key} if self._api_key else {}

    async def health(self) -> KnowledgeHealth:
        started = perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    f"{self._base}/health",
                    headers=self._headers(),
                )
        except httpx.RequestError:
            return KnowledgeHealth(
                status="unavailable",
                latency_ms=_latency_ms(started),
                detail_code="knowledge_service_unavailable",
            )

        latency = _latency_ms(started)
        if response.status_code in {401, 403}:
            return KnowledgeHealth(
                status="unauthorized",
                latency_ms=latency,
                detail_code="knowledge_service_unauthorized",
            )
        if response.status_code == 429 or response.status_code >= 500:
            return KnowledgeHealth(
                status="unavailable",
                latency_ms=latency,
                detail_code="knowledge_service_unavailable",
            )
        if not 200 <= response.status_code < 300:
            return KnowledgeHealth(
                status="invalid",
                latency_ms=latency,
                detail_code="knowledge_service_http_error",
            )
        return KnowledgeHealth(status="healthy", latency_ms=latency)

    async def query(self, request: KnowledgeQuery) -> KnowledgeAnswer:
        started = perf_counter()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    f"{self._base}/query",
                    headers=self._headers(),
                    json={
                        "query": request.question,
                        "user_prompt": request.user_prompt,
                        "conversation_history": request.conversation_history,
                        "include_references": request.include_references,
                        "response_type": request.response_type,
                    },
                )
        except httpx.RequestError as exc:
            raise KnowledgeServiceError("knowledge_service_unavailable") from exc

        if not 200 <= response.status_code < 300:
            raise KnowledgeServiceError(_query_error_code(response))

        try:
            payload = response.json()
        except ValueError as exc:
            raise KnowledgeServiceError("knowledge_response_invalid") from exc
        if not isinstance(payload, dict):
            raise KnowledgeServiceError("knowledge_response_invalid")

        text = payload.get("response")
        if not isinstance(text, str):
            raise KnowledgeServiceError("knowledge_response_invalid")
        references = payload.get("references", [])
        if not isinstance(references, list):
            references = []

        safe_references = [item for item in references if isinstance(item, dict)]
        return KnowledgeAnswer(
            text=text,
            references=safe_references,
            latency_ms=_latency_ms(started),
        )

    async def test_connection(self) -> KnowledgeHealth:
        return await self.health()

    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {"query", "references", "conversation_history", "user_prompt", "webui"}
        )


class FakeKnowledgeServiceClient:
    def __init__(
        self,
        *,
        answer: KnowledgeAnswer | None = None,
        health_result: KnowledgeHealth | None = None,
    ) -> None:
        self.answer = answer or KnowledgeAnswer(text="", references=[], latency_ms=0)
        self.health_result = health_result or KnowledgeHealth("healthy", 0)
        self.queries: list[KnowledgeQuery] = []

    async def health(self) -> KnowledgeHealth:
        return self.health_result

    async def query(self, request: KnowledgeQuery) -> KnowledgeAnswer:
        self.queries.append(request)
        return self.answer

    async def test_connection(self) -> KnowledgeHealth:
        return await self.health()

    def capabilities(self) -> frozenset[str]:
        return frozenset(
            {"query", "references", "conversation_history", "user_prompt", "webui"}
        )
