"use client";

import { useEffect, useMemo, useState } from "react";
import { Pencil, Plus, Save, Trash2, X } from "lucide-react";
import { Button, Skeleton } from "@/components/ui";
import {
  useCreateTechnician,
  useDeactivateTechnician,
  useTechnicians,
  useUpdateTechnician,
} from "@/hooks";
import type {
  Technician,
  TechnicianCreateInput,
  TechnicianUpdateInput,
} from "@/types";

/**
 * Admin CRUD list for Technicians.
 *
 * The list is small by design (a handful of techs), so the UI is a
 * plain table + inline create/edit form — no pagination, no search.
 */
export default function TechniciansPage() {
  const [includeInactive, setIncludeInactive] = useState(false);
  const { data, isLoading, isError, refetch } = useTechnicians({
    include_inactive: includeInactive,
  });

  const create = useCreateTechnician();
  const deactivate = useDeactivateTechnician();

  const [editingId, setEditingId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [banner, setBanner] = useState<
    { kind: "success" | "error"; text: string } | null
  >(null);

  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 3000);
    return () => clearTimeout(t);
  }, [banner]);

  const rows = useMemo(() => data?.items ?? [], [data]);

  return (
    <div className="bg-background flex h-full flex-col overflow-hidden rounded-lg border">
      <div className="flex items-start justify-between gap-2 border-b px-4 py-3 sm:px-6">
        <div>
          <h1 className="text-sm font-semibold tracking-wide uppercase">
            Technicians
          </h1>
          <p className="text-muted-foreground mt-1 text-xs">
            Small admin list of the techs that receive dispatched jobs.
            Each tech can be bound to one WhatsApp chat (the dispatch
            group). Soft-deleting keeps the audit trail intact.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-muted-foreground flex items-center gap-1 text-xs">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => setIncludeInactive(e.target.checked)}
            />
            Show inactive
          </label>
          <Button
            variant="default"
            size="sm"
            onClick={() => setShowCreate((v) => !v)}
          >
            {showCreate ? <X className="h-3.5 w-3.5" /> : <Plus className="h-3.5 w-3.5" />}
            {showCreate ? "Cancel" : "New tech"}
          </Button>
        </div>
      </div>

      {banner ? (
        <div
          className={`px-4 py-2 text-xs sm:px-6 ${
            banner.kind === "success"
              ? "bg-green-50 text-green-800 dark:bg-green-900/30 dark:text-green-200"
              : "bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-200"
          }`}
          role="status"
        >
          {banner.text}
        </div>
      ) : null}

      {showCreate ? (
        <div className="border-b bg-muted/30 px-4 py-3 sm:px-6">
          <CreateTechnicianForm
            onCancel={() => setShowCreate(false)}
            onSubmit={(body) =>
              create.mutate(body, {
                onSuccess: () => {
                  setBanner({ kind: "success", text: "Technician created." });
                  setShowCreate(false);
                },
                onError: (err) =>
                  setBanner({
                    kind: "error",
                    text: `Failed: ${err instanceof Error ? err.message : "unknown error"}`,
                  }),
              })
            }
            loading={create.isPending}
          />
        </div>
      ) : null}

      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : isError ? (
          <div className="text-muted-foreground flex flex-col items-center gap-2 p-8 text-center text-xs">
            <p>Failed to load technicians.</p>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground p-8 text-center text-xs">
            {includeInactive
              ? "No technicians yet — create the first one."
              : "No active technicians. Toggle 'Show inactive' or create one."}
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-muted/50 sticky top-0 z-10">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Name</th>
                <th className="px-3 py-2 text-left font-medium">Phone</th>
                <th className="px-3 py-2 text-left font-medium">Dispatch chat</th>
                <th className="px-3 py-2 text-left font-medium">Status</th>
                <th className="px-3 py-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((t) =>
                editingId === t.id ? (
                  <tr key={t.id} className="bg-muted/30 border-b">
                    <td colSpan={5} className="px-3 py-2">
                      <EditTechnicianForm
                        tech={t}
                        onCancel={() => setEditingId(null)}
                        onSaved={() => {
                          setBanner({
                            kind: "success",
                            text: `Updated ${t.name}.`,
                          });
                          setEditingId(null);
                        }}
                        onError={(msg) =>
                          setBanner({ kind: "error", text: `Failed: ${msg}` })
                        }
                      />
                    </td>
                  </tr>
                ) : (
                  <tr key={t.id} className="border-b">
                    <td className="px-3 py-2 font-medium">{t.name}</td>
                    <td className="text-muted-foreground px-3 py-2 font-mono text-xs">
                      {t.phone_e164 ?? "—"}
                    </td>
                    <td className="text-muted-foreground px-3 py-2 font-mono text-[11px]">
                      {t.whatsapp_chat_jid ?? "—"}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      <span
                        className={
                          t.is_active
                            ? "text-green-700 dark:text-green-300"
                            : "text-muted-foreground"
                        }
                      >
                        {t.is_active ? "Active" : "Inactive"}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <div className="flex justify-end gap-1.5">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setEditingId(t.id)}
                          disabled={deactivate.isPending}
                          className="h-7 text-xs"
                        >
                          <Pencil className="h-3.5 w-3.5" />
                          Edit
                        </Button>
                        {t.is_active ? (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() =>
                              deactivate.mutate(t.id, {
                                onSuccess: () =>
                                  setBanner({
                                    kind: "success",
                                    text: `Deactivated ${t.name}.`,
                                  }),
                                onError: (err) =>
                                  setBanner({
                                    kind: "error",
                                    text: `Failed: ${err instanceof Error ? err.message : "unknown error"}`,
                                  }),
                              })
                            }
                            disabled={deactivate.isPending}
                            className="h-7 text-xs"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                            Deactivate
                          </Button>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                )
              )}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

function CreateTechnicianForm({
  onSubmit,
  onCancel,
  loading,
}: {
  onSubmit: (body: TechnicianCreateInput) => void;
  onCancel: () => void;
  loading: boolean;
}) {
  const [name, setName] = useState("");
  const [phone, setPhone] = useState("");
  const [chatJid, setChatJid] = useState("");
  const [notes, setNotes] = useState("");

  const submit = () => {
    if (!name.trim()) return;
    onSubmit({
      name: name.trim(),
      phone_e164: phone.trim() || null,
      whatsapp_chat_jid: chatJid.trim() || null,
      is_active: true,
      notes: notes.trim() || null,
    });
  };

  return (
    <div className="space-y-2">
      <h2 className="text-xs font-semibold tracking-wide uppercase">
        New technician
      </h2>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name *"
          aria-label="Name"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm"
        />
        <input
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="Phone (E.164)"
          aria-label="Phone"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm"
        />
        <input
          value={chatJid}
          onChange={(e) => setChatJid(e.target.value)}
          placeholder="WhatsApp chat JID"
          aria-label="WhatsApp chat JID"
          className="border-input bg-background h-8 rounded-md border px-2 font-mono text-sm"
        />
        <input
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Notes"
          aria-label="Notes"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm"
        />
      </div>
      <div className="flex justify-end gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={onCancel}
          disabled={loading}
        >
          Cancel
        </Button>
        <Button
          variant="default"
          size="sm"
          onClick={submit}
          disabled={loading || name.trim().length === 0}
        >
          <Save className="h-3.5 w-3.5" />
          {loading ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}

function EditTechnicianForm({
  tech,
  onCancel,
  onSaved,
  onError,
}: {
  tech: Technician;
  onCancel: () => void;
  onSaved: () => void;
  onError: (msg: string) => void;
}) {
  const update = useUpdateTechnician(tech.id);
  const [name, setName] = useState(tech.name);
  const [phone, setPhone] = useState(tech.phone_e164 ?? "");
  const [chatJid, setChatJid] = useState(tech.whatsapp_chat_jid ?? "");
  const [isActive, setIsActive] = useState(tech.is_active);
  const [notes, setNotes] = useState(tech.notes ?? "");

  const submit = () => {
    if (!name.trim()) return;
    const body: TechnicianUpdateInput = {
      name: name.trim(),
      phone_e164: phone.trim() || null,
      whatsapp_chat_jid: chatJid.trim() || null,
      is_active: isActive,
      notes: notes.trim() || null,
    };
    update.mutate(body, {
      onSuccess: onSaved,
      onError: (err) =>
        onError(err instanceof Error ? err.message : "unknown error"),
    });
  };

  const loading = update.isPending;

  return (
    <div className="space-y-2">
      <h2 className="text-xs font-semibold tracking-wide uppercase">
        Edit technician
      </h2>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name *"
          aria-label="Name"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm"
        />
        <input
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="Phone (E.164)"
          aria-label="Phone"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm"
        />
        <input
          value={chatJid}
          onChange={(e) => setChatJid(e.target.value)}
          placeholder="WhatsApp chat JID"
          aria-label="WhatsApp chat JID"
          className="border-input bg-background h-8 rounded-md border px-2 font-mono text-sm"
        />
        <label className="text-muted-foreground flex items-center gap-1 text-xs">
          <input
            type="checkbox"
            checked={isActive}
            onChange={(e) => setIsActive(e.target.checked)}
          />
          Active
        </label>
        <input
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Notes"
          aria-label="Notes"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm sm:col-span-2"
        />
      </div>
      <div className="flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onCancel} disabled={loading}>
          Cancel
        </Button>
        <Button
          variant="default"
          size="sm"
          onClick={submit}
          disabled={loading || name.trim().length === 0}
        >
          <Save className="h-3.5 w-3.5" />
          {loading ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}