from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Channel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(index=True, unique=True)
    enabled: bool = False
    display_name: str
    status: str = "not_configured"
    credential_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Rule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    enabled: bool = True
    priority: int = Field(default=100, index=True)
    match_type: str = "contains"
    pattern: str = Field(index=True)
    condition_logic: str = "and"
    conditions: str = "[]"
    reply_text: str
    escalate: bool = False
    escalate_message: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class AssistantSettings(SQLModel, table=True):
    id: Optional[int] = Field(default=1, primary_key=True)
    system_prompt: str = "Answer the question using the provided context. If the context contains relevant information, use it to answer. If the context is missing or insufficient, say that the information is not available in the knowledge base rather than making up an answer."
    fallback_reply: str = "I don't have enough information yet. Please contact our staff."
    rag_enabled: bool = True
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    vision_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    admin_notify_channel: str = "telegram"
    admin_notify_chat_id: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class KnowledgeDocument(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    filename: str
    path: str
    rag_doc_id: str = ""
    status: str = "pending"
    error: str = ""
    indexed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChatEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(index=True)
    external_user_id: str = ""
    incoming_text: str
    decision_source: str
    reply_text: str = ""
    raw_event: str = "{}"
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now, index=True)
