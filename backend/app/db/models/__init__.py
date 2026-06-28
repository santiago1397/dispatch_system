"""Database models."""

# ruff: noqa: I001, RUF022 - Imports structured for Jinja2 template conditionals
from app.db.models.user import User
from app.db.models.session import Session
from app.db.models.item import Item
from app.db.models.conversation import Conversation, Message, ToolCall
from app.db.models.openphone import IncomingMessage
from app.db.models.company import Company, PatternType
from app.db.models.company_phone_binding import CompanyPhoneBinding
from app.db.models.dispatch_job import DispatchJob, ClassificationStatus
from app.db.models.job import Job
from app.db.models.openphone import MessageSource
from app.db.models.whatsapp import WhatsappMessage, WhatsappTrackedChat
from app.db.models.app_settings import AppSettings
from app.db.models.technician import Technician
from app.db.models.job_lifecycle_event import (
    JobLifecycleEvent,
    LifecycleEventSource,
)
from app.db.models.alert import Alert, AlertKind
from app.db.models.daily_stats import DailyStatsSnapshot, StatsScope

__all__ = [
    "AppSettings",
    "User",
    "Session",
    "Item",
    "Conversation",
    "Message",
    "ToolCall",
    "IncomingMessage",
    "MessageSource",
    "Company",
    "CompanyPhoneBinding",
    "PatternType",
    "DispatchJob",
    "ClassificationStatus",
    "Job",
    "WhatsappMessage",
    "WhatsappTrackedChat",
    "Technician",
    "JobLifecycleEvent",
    "LifecycleEventSource",
    "Alert",
    "AlertKind",
    "DailyStatsSnapshot",
    "StatsScope",
]
