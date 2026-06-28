"""Repository layer for database operations."""
# ruff: noqa: I001, RUF022 - Imports structured for Jinja2 template conditionals

from app.repositories.base import BaseRepository

from app.repositories import user as user_repo

from app.repositories import session as session_repo

from app.repositories import item as item_repo

from app.repositories import conversation as conversation_repo

from app.repositories import openphone as openphone_repo

from app.repositories import company as company_repo

from app.repositories import company_phone_binding as phone_binding_repo

from app.repositories import dispatch_job as dispatch_job_repo

from app.repositories import job as job_repo

from app.repositories import whatsapp as whatsapp_repo

from app.repositories import app_settings as app_settings_repo

from app.repositories import technician as technician_repo

from app.repositories import job_lifecycle_event as lifecycle_event_repo

from app.repositories import alert as alert_repo

from app.repositories import daily_stats as daily_stats_repo

__all__ = [
    "BaseRepository",
    "user_repo",
    "session_repo",
    "item_repo",
    "conversation_repo",
    "openphone_repo",
    "company_repo",
    "phone_binding_repo",
    "dispatch_job_repo",
    "job_repo",
    "whatsapp_repo",
    "app_settings_repo",
    "technician_repo",
    "lifecycle_event_repo",
    "alert_repo",
    "daily_stats_repo",
]
