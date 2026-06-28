/**
 * Application constants.
 */

export const APP_NAME = "agents_bots";
export const APP_DESCRIPTION = "Project to work as microservice axuliar for main application";

// API Routes (Next.js internal routes)
export const API_ROUTES = {
  // Auth
  LOGIN: "/auth/login",
  REGISTER: "/auth/register",
  LOGOUT: "/auth/logout",
  REFRESH: "/auth/refresh",
  ME: "/auth/me",

  // Health
  HEALTH: "/health",

  // Users
  USERS: "/users",

  // WhatsApp (scraper ingest — read-only from the frontend)
  WHATSAPP_TRACKED_CHATS: "/whatsapp/tracked-chats",
  WHATSAPP_MESSAGES: "/whatsapp/messages",

  // Jobs (operator review of classified dispatches)
  JOBS: "/dispatch/jobs",
  JOB_DETAIL: (id: string) => `/dispatch/jobs/${id}`,
  JOB_RECLASSIFY: (id: string) => `/dispatch/jobs/${id}/reclassify`,
  JOB_REMATCH_CLOSING: (id: string) => `/dispatch/jobs/${id}/rematch-closing`,

  // Companies (read-only list for the Jobs page filter dropdown)
  COMPANIES: "/companies",

  // Phone -> company bindings (operator-curated classification tier)
  PHONE_BINDINGS: "/phone-bindings",
  PHONE_BINDING: (id: string) => `/phone-bindings/${id}`,
  PHONE_BINDING_SUGGESTIONS: "/phone-bindings/suggestions",

  // Incoming messages (source record behind every dispatch job)
  INCOMING_MESSAGE: (id: string) => `/openphone/incoming/${id}`,

  // Application settings (admin only)
  SETTINGS_LLM: "/settings/llm",

  // Job lifecycle — manual override dropdown (state-correct only) + timeline read.
  // NOTE: there are intentionally no outbound/send routes here. The system
  // never places customer messages — see memory/feedback_no_outbound_automation.md.
  JOBS_LIFECYCLE: (id: string) => `/dispatch/jobs/${id}/lifecycle`,

  // Technicians — admin CRUD.
  TECHNICIANS: "/technicians",
  TECHNICIAN: (id: string) => `/technicians/${id}`,

  // Tracked chats — already supported on backend; frontend re-uses the
  // existing /whatsapp/tracked-chats list endpoint and just adds a chat_role
  // dropdown to it.

  // Alerts — pipeline-health dashboard.
  ALERTS: "/alerts",
  ALERT: (id: string) => `/alerts/${id}`,
  ALERT_RESOLVE: (id: string) => `/alerts/${id}/resolve`,

  // Daily stats — pre-computed rollups + CSV/JSON export.
  STATS: "/stats",
  STATS_EXPORT: "/stats/export",
} as const;

// Navigation routes
export const ROUTES = {
  HOME: "/",
  LOGIN: "/login",
  DASHBOARD: "/dashboard",
  JOBS: "/jobs",
  WHATSAPP: "/whatsapp",
  PROFILE: "/profile",
  SETTINGS: "/settings",
  ALERTS: "/alerts",
  STATS: "/stats",
  CHAT_ROLES: "/dispatch/chat-roles",
  TECHNICIANS: "/technicians",
} as const;

// WebSocket URL (for chat - this needs to be direct to backend for WS)
export const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8888";
