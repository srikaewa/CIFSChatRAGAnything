import json
from datetime import timedelta

from sqlmodel import Session, select

from chatbot_manager.models import Conversation, ConversationMessage, utc_now


class ConversationService:
    def __init__(self, session: Session, inactivity_hours: int = 24) -> None:
        self._session = session
        self._inactivity_hours = inactivity_hours

    def get_or_create(
        self,
        bot_id: int,
        channel_connection_id: int,
        external_user_id: str,
    ) -> Conversation:
        cutoff = utc_now() - timedelta(hours=self._inactivity_hours)
        conversation = self._session.exec(
            select(Conversation)
            .where(Conversation.bot_id == bot_id)
            .where(Conversation.channel_connection_id == channel_connection_id)
            .where(Conversation.external_user_id == external_user_id)
            .where(Conversation.status != "closed")
            .where(Conversation.last_message_at >= cutoff)
            .order_by(Conversation.last_message_at.desc(), Conversation.id.desc())
        ).first()
        if conversation is not None:
            return conversation

        conversation = Conversation(
            bot_id=bot_id,
            channel_connection_id=channel_connection_id,
            external_user_id=external_user_id,
            status="bot_active",
        )
        self._session.add(conversation)
        self._session.commit()
        self._session.refresh(conversation)
        return conversation

    def is_duplicate(self, conversation_id: int, external_message_id: str) -> bool:
        if not external_message_id:
            return False
        existing = self._session.exec(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .where(ConversationMessage.sender_type == "user")
            .where(ConversationMessage.external_message_id == external_message_id)
        ).first()
        return existing is not None

    def record_inbound(
        self,
        conversation: Conversation,
        external_message_id: str,
        text: str,
        metadata: dict[str, object],
    ) -> ConversationMessage:
        if conversation.id is None:
            raise ValueError("Conversation must be persisted")
        if external_message_id:
            existing = self._session.exec(
                select(ConversationMessage)
                .where(ConversationMessage.conversation_id == conversation.id)
                .where(ConversationMessage.sender_type == "user")
                .where(ConversationMessage.external_message_id == external_message_id)
            ).first()
            if existing is not None:
                return existing

        message = ConversationMessage(
            conversation_id=conversation.id,
            sender_type="user",
            content=text,
            external_message_id=external_message_id,
            metadata_json=json.dumps(metadata, sort_keys=True, default=str),
        )
        now = utc_now()
        conversation.last_message_at = now
        self._session.add(message)
        self._session.add(conversation)
        self._session.commit()
        self._session.refresh(message)
        self._session.refresh(conversation)
        return message

    def history(self, conversation_id: int, limit: int = 12) -> list[dict[str, str]]:
        if limit <= 0:
            return []
        rows = self._session.exec(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation_id)
            .where(ConversationMessage.sender_type.in_(["user", "bot", "operator"]))
            .order_by(ConversationMessage.created_at.desc(), ConversationMessage.id.desc())
            .limit(limit)
        ).all()
        rows.reverse()
        role_by_sender = {
            "user": "user",
            "bot": "assistant",
            "operator": "assistant",
        }
        return [
            {"role": role_by_sender[row.sender_type], "content": row.content}
            for row in rows
        ]
