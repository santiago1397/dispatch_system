/**
 * Types for IncomingMessage — the source record behind every DispatchJob.
 * Mirrors `backend/app/schemas/openphone.py::IncomingMessageRead`.
 *
 * The Jobs page fetches the IncomingMessage for the body and metadata
 * (source channel, sender phone, arrival time) so the operator can
 * compare the AI's extracted fields against the original message.
 */

export const MESSAGE_SOURCES = ["openphone", "whatsapp"] as const;

export type MessageSource = (typeof MESSAGE_SOURCES)[number];

export interface IncomingMessage {
  id: string;
  source: MessageSource;
  openphone_id: string | null;
  direction: string | null;
  from_number: string | null;
  to_numbers: string[];
  content: string | null;
  status: string | null;
  event_type: string | null;
  phone_number_id: string | null;
  created_at: string;
}

export const SOURCE_LABEL: Record<MessageSource, string> = {
  openphone: "OpenPhone",
  whatsapp: "WhatsApp",
};

/**
 * One row per OpenPhone conversation counterparty — derived server-side
 * (no stored chat/thread id). Mirrors
 * `backend/app/schemas/openphone.py::OpenPhoneThreadSummary`.
 */
export interface OpenPhoneThreadSummary {
  counterparty: string;
  last_content: string | null;
  last_direction: string | null;
  last_created_at: string;
  message_count: number;
}

export interface OpenPhoneThreadList {
  items: OpenPhoneThreadSummary[];
  total: number;
}
