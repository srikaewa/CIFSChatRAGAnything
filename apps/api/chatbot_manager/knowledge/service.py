from sqlmodel import Session

from chatbot_manager.credentials import read_credential
from chatbot_manager.models import KnowledgeService

from .client import KnowledgeServiceClient, LightRAGClient


def build_knowledge_client(
    session: Session,
    knowledge_service_id: int,
) -> KnowledgeServiceClient:
    service = session.get(KnowledgeService, knowledge_service_id)
    if service is None or not service.enabled:
        raise LookupError("Knowledge Service not available")
    if service.service_type != "lightrag":
        raise ValueError("unsupported_knowledge_service_type")

    secrets = read_credential(session, service.credential_id)
    return LightRAGClient(service.api_base_url, secrets.get("api_key", ""))
