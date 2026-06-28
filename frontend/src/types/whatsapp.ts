/**
 * Types for the WhatsApp ingestion view in the frontend.
 * Mirrors `backend/app/schemas/whatsapp.py`.
 */

export interface WhatsappTrackedChat {
  id: string;
  chat_jid: string;
  display_name: string;
  is_group: boolean;
  is_active: boolean;
  /**
   * Routing tag set by the /chat-roles admin page.
   * ``tech_dispatch`` makes the chat a candidate for the operator
   * dispatch detector; ``other`` (default) keeps the chat on the
   * customer-facing mirror + classify path.
   */
  chat_role: string;
  last_scraped_at: string | null;
  last_seen_message_id: string | null;
  created_at: string;
  updated_at: string;
}

export const CHAT_ROLES = ["other", "tech_dispatch"] as const;

export type ChatRole = (typeof CHAT_ROLES)[number];

export const CHAT_ROLE_LABEL: Record<ChatRole, string> = {
  other: "Other (customer-facing)",
  tech_dispatch: "Tech dispatch",
};

export interface WhatsappTrackedChatList {
  items: WhatsappTrackedChat[];
  total: number;
}

export interface WhatsappMessage {
  id: string;
  wa_message_id: string;
  chat_jid: string;
  sender_jid: string | null;
  sender_name: string | null;
  is_from_me: boolean;
  body: string | null;
  timestamp: string;
  edited_at: string | null;
  is_deleted: boolean;
  quoted_wa_message_id: string | null;
  media_type: string | null;
  media_mime: string | null;
  media_filename: string | null;
  media_size_bytes: number | null;
  media_caption: string | null;
  media_url: string | null;
  reactions: Array<Record<string, unknown>>;
  is_system_message: boolean;
  system_event_type: string | null;
  raw_payload: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface WhatsappMessageList {
  items: WhatsappMessage[];
  total: number;
}
