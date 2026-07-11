"""API v1 router aggregation."""
# ruff: noqa: I001 - Imports structured for Jinja2 template conditionals

from fastapi import APIRouter

from app.api.routes.v1 import health
from app.api.routes.v1 import auth, users
from app.api.routes.v1 import sessions
from app.api.routes.v1 import items
from app.api.routes.v1 import conversations
from app.api.routes.v1 import agent
from app.api.routes.v1 import openphone
from app.api.routes.v1 import dispatch_jobs
from app.api.routes.v1 import whatsapp
from app.api.routes.v1 import companies
from app.api.routes.v1 import phone_bindings
from app.api.routes.v1 import app_settings
from app.api.routes.v1 import technicians
from app.api.routes.v1 import alerts
from app.api.routes.v1 import stats
from app.api.routes.v1 import reports

v1_router = APIRouter()

# Health check routes (no auth required)
v1_router.include_router(health.router, tags=["health"])

# Authentication routes
v1_router.include_router(auth.router, prefix="/auth", tags=["auth"])

# User routes
v1_router.include_router(users.router, prefix="/users", tags=["users"])

# Session management routes
v1_router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])

# Example CRUD routes (items)
v1_router.include_router(items.router, prefix="/items", tags=["items"])

# Conversation routes (AI chat persistence)
v1_router.include_router(conversations.router, prefix="/conversations", tags=["conversations"])

# AI Agent routes
v1_router.include_router(agent.router, tags=["agent"])

# OpenPhone (Quo API) routes — webhook + proxy
v1_router.include_router(openphone.router, prefix="/openphone", tags=["openphone"])

# Dispatch jobs — classified jobs from webhooks
v1_router.include_router(dispatch_jobs.router, prefix="/dispatch", tags=["dispatch"])

# WhatsApp Web scraper ingestion — service-token + batch ingest + tracked-chat CRUD
v1_router.include_router(whatsapp.router, prefix="/whatsapp", tags=["whatsapp"])

# Companies — read-only list for the Jobs page filter dropdown
v1_router.include_router(companies.router, prefix="/companies", tags=["companies"])

# Phone -> company bindings — operator-curated third classification tier
v1_router.include_router(phone_bindings.router, prefix="/phone-bindings", tags=["phone-bindings"])

# Application settings — admin-only LLM config override
v1_router.include_router(app_settings.router, prefix="/settings", tags=["settings"])

# Technician CRUD — admin only. Operator-facing /dispatch/technicians page.
v1_router.include_router(technicians.router, prefix="/technicians", tags=["technicians"])

# Alerts dashboard — pipeline-health open issues (stuck jobs, missing
# closings, unattributed replies). The engine itself runs in a scheduler
# cron (see main.py:lifespan); this router is just the read + resolve UI.
v1_router.include_router(alerts.router, prefix="/alerts", tags=["alerts"])

# Daily stats — pre-computed rollups written by the daily-stats service.
# The router exposes read + CSV/JSON export for the /stats page.
v1_router.include_router(stats.router, prefix="/stats", tags=["stats"])

# Live per-company job-status report — computed on every request, no
# snapshot table, so "today" is always current. Backs the /reports page.
v1_router.include_router(reports.router, prefix="/reports", tags=["reports"])
