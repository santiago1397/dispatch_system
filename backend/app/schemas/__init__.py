"""Pydantic schemas."""
# ruff: noqa: I001, RUF022 - Imports structured for Jinja2 template conditionals

from app.schemas.token import Token, TokenPayload
from app.schemas.user import UserCreate, UserRead, UserUpdate

from app.schemas.session import SessionRead, SessionListResponse, LogoutAllResponse

from app.schemas.item import ItemCreate, ItemRead, ItemUpdate

from app.schemas.conversation import (
    ConversationCreate,
    ConversationRead,
    ConversationUpdate,
    MessageCreate,
    MessageRead,
    ToolCallRead,
)

from app.schemas.whatsapp import (
    ServiceRefreshRequest,
    ServiceTokenRequest,
    ServiceTokenResponse,
    WhatsappMessageBatchError,
    WhatsappMessageBatchIngest,
    WhatsappMessageBatchResult,
    WhatsappMessageCreate,
    WhatsappMessageList,
    WhatsappMessageRead,
    WhatsappTrackedChatCreate,
    WhatsappTrackedChatDiscoverItem,
    WhatsappTrackedChatList,
    WhatsappTrackedChatRead,
    WhatsappTrackedChatUpdate,
)

from app.schemas.technician import (
    TechnicianCreate,
    TechnicianList,
    TechnicianRead,
    TechnicianUpdate,
)

from app.schemas.job_lifecycle_event import (
    JobLifecycleEventList,
    JobLifecycleEventRead,
    LifecycleTransitionIn,
)

from app.schemas.alert import AlertList, AlertRead

from app.schemas.daily_stats import DailyStatsList, DailyStatsSnapshotRead

__all__ = [
    "UserCreate",
    "UserRead",
    "UserUpdate",
    "Token",
    "TokenPayload",
    "SessionRead",
    "SessionListResponse",
    "LogoutAllResponse",
    "ItemCreate",
    "ItemRead",
    "ItemUpdate",
    "ConversationCreate",
    "ConversationRead",
    "ConversationUpdate",
    "MessageCreate",
    "MessageRead",
    "ToolCallRead",
    "ServiceRefreshRequest",
    "ServiceTokenRequest",
    "ServiceTokenResponse",
    "WhatsappMessageBatchError",
    "WhatsappMessageBatchIngest",
    "WhatsappMessageBatchResult",
    "WhatsappMessageCreate",
    "WhatsappMessageList",
    "WhatsappMessageRead",
    "WhatsappTrackedChatCreate",
    "WhatsappTrackedChatDiscoverItem",
    "WhatsappTrackedChatList",
    "WhatsappTrackedChatRead",
    "WhatsappTrackedChatUpdate",
    "TechnicianCreate",
    "TechnicianList",
    "TechnicianRead",
    "TechnicianUpdate",
    "JobLifecycleEventList",
    "JobLifecycleEventRead",
    "LifecycleTransitionIn",
    "AlertList",
    "AlertRead",
    "DailyStatsList",
    "DailyStatsSnapshotRead",
]
