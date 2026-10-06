from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    email: str = Field(index=True, unique=True)
    password_hash: str
    role: str = Field(default="operator", index=True)
    active: bool = True
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class UserBotAccess(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(index=True)
    bot_id: int = Field(index=True)
    created_at: datetime = Field(default_factory=utc_now)


class AuditEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    actor_user_id: Optional[int] = Field(default=None, index=True)
    action: str = Field(index=True)
    object_type: str = Field(index=True)
    object_id: str = Field(index=True)
    bot_id: Optional[int] = Field(default=None, index=True)
    summary: str
    before_json: str = "{}"
    after_json: str = "{}"
    request_metadata_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now, index=True)


class Bot(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    description: str = ""
    lifecycle_status: str = Field(default="draft", index=True)
    live_config_version_id: Optional[int] = Field(default=None, index=True)
    draft_config_version_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BotConfigVersion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    version_number: int = Field(index=True)
    status: str = Field(default="draft", index=True)
    system_prompt: str = ""
    tone: str = "professional"
    language: str = "auto"
    response_style: str = "concise"
    fallback_reply: str = ""
    fallback_policy: str = "reply"
    escalation_policy: str = "{}"
    custom_instructions: str = ""
    knowledge_service_id: Optional[int] = Field(default=None, index=True)
    created_by: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    published_at: Optional[datetime] = None


class BotConfigRule(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    config_version_id: int = Field(index=True)
    name: str = ""
    enabled: bool = True
    priority: int = Field(default=100, index=True)
    match_type: str = "contains"
    pattern: str = Field(index=True)
    condition_logic: str = "and"
    conditions: str = "[]"
    action: str = "RESPOND"
    reply_text: str = ""
    escalate_message: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Credential(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    credential_type: str = Field(index=True)
    encrypted_payload: str
    created_at: datetime = Field(default_factory=utc_now)
    rotated_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None


class KnowledgeService(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    service_type: str = Field(default="lightrag", index=True)
    api_base_url: str
    webui_url: str = ""
    credential_id: Optional[int] = Field(default=None, index=True)
    enabled: bool = True
    health_status: str = Field(default="unknown", index=True)
    last_health_check: Optional[datetime] = None
    metadata_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ChannelConnection(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    provider: str = Field(index=True)
    display_name: str
    external_account_id: str = Field(default="", index=True)
    webhook_key: str = Field(index=True, unique=True)
    credential_id: Optional[int] = Field(default=None, index=True)
    enabled: bool = False
    status: str = Field(default="not_configured", index=True)
    last_health_check: Optional[datetime] = None
    metadata_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class Conversation(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    channel_connection_id: int = Field(index=True)
    external_user_id: str = Field(index=True)
    status: str = Field(default="bot_active", index=True)
    assigned_operator_id: Optional[int] = Field(default=None, index=True)
    handoff_reason: str = ""
    started_at: datetime = Field(default_factory=utc_now)
    last_message_at: datetime = Field(default_factory=utc_now, index=True)
    closed_at: Optional[datetime] = None


class ConversationMessage(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(index=True)
    sender_type: str = Field(index=True)
    content: str
    external_message_id: str = Field(default="", index=True)
    delivery_status: str = ""
    metadata_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now, index=True)


class ConversationHandoffEvent(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    conversation_id: int = Field(index=True)
    event_type: str = Field(index=True)
    actor_user_id: Optional[int] = Field(default=None, index=True)
    reason: str = ""
    created_at: datetime = Field(default_factory=utc_now, index=True)


class BotDecision(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    message_id: int = Field(index=True)
    bot_id: int = Field(index=True)
    config_version_id: int = Field(index=True)
    decision_type: str = Field(index=True)
    rule_id: Optional[int] = Field(default=None, index=True)
    knowledge_service_id: Optional[int] = Field(default=None, index=True)
    reference_count: int = 0
    retrieval_latency_ms: Optional[int] = None
    llm_latency_ms: Optional[int] = None
    total_latency_ms: int = 0
    error_code: str = Field(default="", index=True)
    metadata_json: str = "{}"
    created_at: datetime = Field(default_factory=utc_now, index=True)


class Incident(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    severity: str = Field(index=True)
    source_type: str = Field(index=True)
    source_id: str = Field(index=True)
    incident_type: str = Field(index=True)
    deduplication_key: str = Field(index=True)
    status: str = Field(default="open", index=True)
    first_seen_at: datetime = Field(default_factory=utc_now)
    last_seen_at: datetime = Field(default_factory=utc_now)
    resolved_at: Optional[datetime] = None
    details_json: str = "{}"
    affected_bot_ids_json: str = "[]"
    external_notified_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=utc_now)


class BotTestCase(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    name: str = ""
    input_message: str
    expected_behavior_json: str = "{}"
    tags_json: str = "[]"
    enabled: bool = Field(default=True, index=True)
    created_by: str = ""
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class BotTestRun(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    bot_id: int = Field(index=True)
    config_version_id: int = Field(index=True)
    status: str = Field(default="running", index=True)
    actor: str = ""
    started_at: datetime = Field(default_factory=utc_now, index=True)
    completed_at: Optional[datetime] = None


class BotTestResult(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    test_run_id: int = Field(index=True)
    test_case_id: int = Field(index=True)
    config_version_id: int = Field(index=True)
    input_message: str
    expected_behavior_json: str = "{}"
    tags_json: str = "[]"
    actual_response: str = ""
    decision_type: str = Field(default="", index=True)
    outcome: str = Field(default="fail", index=True)
    total_latency_ms: int = 0
    reference_count: int = 0
    escalate: bool = False
    error_code: str = ""
    evaluation_details_json: str = "[]"
    created_at: datetime = Field(default_factory=utc_now, index=True)


class Channel(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    provider: str = Field(index=True, unique=True)
    enabled: bool = False
    display_name: str
    status: str = "not_configured"
    credential_json: str = "{}"
    bot_id: Optional[int] = Field(default=None, index=True)
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
    bot_id: Optional[int] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
