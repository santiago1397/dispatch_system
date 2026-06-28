/**
 * Types for the Technicians admin page. Mirrors
 * ``backend/app/schemas/technician.py``.
 */

export interface Technician {
  id: string;
  name: string;
  phone_e164: string | null;
  whatsapp_chat_jid: string | null;
  is_active: boolean;
  notes: string | null;
  created_at: string;
  updated_at: string | null;
}

export interface TechnicianList {
  items: Technician[];
  total: number;
}

export interface TechnicianCreateInput {
  name: string;
  phone_e164?: string | null;
  whatsapp_chat_jid?: string | null;
  is_active?: boolean;
  notes?: string | null;
}

export interface TechnicianUpdateInput {
  name?: string | null;
  phone_e164?: string | null;
  whatsapp_chat_jid?: string | null;
  is_active?: boolean;
  notes?: string | null;
}