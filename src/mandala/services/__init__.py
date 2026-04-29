"""Прикладные сервисы поверх репозиториев (квоты — тикет 7; пользователь — тикет 8)."""

from mandala.services.quota import (
    RESOURCE_IMAGE_GENERATION,
    RESOURCE_TEXT_REPLY,
    QuotaConsumeResult,
    QuotaService,
)
from mandala.services.user_identity import UserIdentityService

__all__ = [
    "RESOURCE_IMAGE_GENERATION",
    "RESOURCE_TEXT_REPLY",
    "QuotaConsumeResult",
    "QuotaService",
    "UserIdentityService",
]
