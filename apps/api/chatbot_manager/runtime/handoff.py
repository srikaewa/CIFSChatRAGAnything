from sqlalchemy import update
from sqlmodel import Session

from chatbot_manager.models import Conversation, ConversationHandoffEvent, utc_now


class ConversationStateError(RuntimeError):
    """Stable conversation-state error safe for UI/runtime mapping."""


class HandoffService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def _conversation(self, conversation_id: int) -> Conversation:
        conversation = self._session.get(Conversation, conversation_id)
        if conversation is None:
            raise ConversationStateError("conversation_not_found")
        return conversation

    def _record_event(
        self,
        conversation_id: int,
        event_type: str,
        *,
        actor_user_id: int | None = None,
        reason: str = "",
    ) -> None:
        self._session.add(
            ConversationHandoffEvent(
                conversation_id=conversation_id,
                event_type=event_type,
                actor_user_id=actor_user_id,
                reason=reason,
            )
        )

    def escalate(
        self,
        conversation_id: int,
        *,
        reason: str,
        actor_user_id: int | None = None,
    ) -> Conversation:
        conversation = self._conversation(conversation_id)
        if conversation.status != "bot_active":
            raise ConversationStateError("invalid_conversation_state")
        conversation.status = "needs_human"
        conversation.handoff_reason = reason
        conversation.assigned_operator_id = None
        self._session.add(conversation)
        self._record_event(
            conversation_id,
            "escalated",
            actor_user_id=actor_user_id,
            reason=reason,
        )
        self._session.commit()
        self._session.refresh(conversation)
        return conversation

    def take(self, conversation_id: int, operator_id: int) -> Conversation:
        statement = (
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.status == "needs_human",
            )
            .values(
                status="human_active",
                assigned_operator_id=operator_id,
            )
        )
        result = self._session.exec(statement)
        if result.rowcount != 1:
            self._session.rollback()
            raise ConversationStateError("conversation_not_available")

        self._record_event(
            conversation_id,
            "taken",
            actor_user_id=operator_id,
        )
        self._session.commit()
        return self._conversation(conversation_id)

    def assign(
        self,
        conversation_id: int,
        operator_id: int,
        *,
        actor_user_id: int | None = None,
    ) -> Conversation:
        conversation = self._conversation(conversation_id)
        if conversation.status not in {"needs_human", "human_active"}:
            raise ConversationStateError("invalid_conversation_state")
        conversation.status = "human_active"
        conversation.assigned_operator_id = operator_id
        self._session.add(conversation)
        self._record_event(
            conversation_id,
            "assigned",
            actor_user_id=actor_user_id,
            reason=str(operator_id),
        )
        self._session.commit()
        self._session.refresh(conversation)
        return conversation

    def return_to_bot(
        self,
        conversation_id: int,
        *,
        actor_user_id: int | None = None,
    ) -> Conversation:
        conversation = self._conversation(conversation_id)
        if conversation.status != "human_active":
            raise ConversationStateError("invalid_conversation_state")
        conversation.status = "bot_active"
        conversation.assigned_operator_id = None
        conversation.handoff_reason = ""
        self._session.add(conversation)
        self._record_event(
            conversation_id,
            "returned_to_bot",
            actor_user_id=actor_user_id,
        )
        self._session.commit()
        self._session.refresh(conversation)
        return conversation

    def close(
        self,
        conversation_id: int,
        *,
        actor_user_id: int | None = None,
        reason: str = "",
    ) -> Conversation:
        conversation = self._conversation(conversation_id)
        if conversation.status == "closed":
            raise ConversationStateError("invalid_conversation_state")
        conversation.status = "closed"
        conversation.closed_at = utc_now()
        conversation.assigned_operator_id = None
        if reason:
            conversation.handoff_reason = reason
        self._session.add(conversation)
        self._record_event(
            conversation_id,
            "closed",
            actor_user_id=actor_user_id,
            reason=reason,
        )
        self._session.commit()
        self._session.refresh(conversation)
        return conversation
