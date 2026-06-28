"use client";

import { useEffect, useMemo, useState } from "react";
import { Save } from "lucide-react";
import { Button, Skeleton } from "@/components/ui";
import { useTrackedChats, useUpdateTrackedChat } from "@/hooks";
import {
  CHAT_ROLES,
  CHAT_ROLE_LABEL,
  type ChatRole,
  type WhatsappTrackedChat,
} from "@/types";

export default function ChatRolesPage() {
  const { data: chats, isLoading, isError, refetch } = useTrackedChats();
  const update = useUpdateTrackedChat();

  const [drafts, setDrafts] = useState<Record<string, ChatRole>>({});
  const [banner, setBanner] = useState<
    { kind: "success" | "error"; text: string } | null
  >(null);

  // Auto-dismiss the banner.
  useEffect(() => {
    if (!banner) return;
    const t = setTimeout(() => setBanner(null), 3000);
    return () => clearTimeout(t);
  }, [banner]);

  const rows = useMemo(() => chats ?? [], [chats]);

  // Resync local drafts when the chat list first loads. Once the
  // operator changes a dropdown we keep that draft until they save.
  useEffect(() => {
    if (rows.length === 0) return;
    setDrafts((prev) => {
      const next = { ...prev };
      for (const c of rows) {
        if (!(c.chat_jid in next)) {
          const role = (CHAT_ROLES as readonly string[]).includes(c.chat_role)
            ? (c.chat_role as ChatRole)
            : "other";
          next[c.chat_jid] = role;
        }
      }
      return next;
    });
  }, [rows]);

  const dirty = (c: WhatsappTrackedChat) =>
    (drafts[c.chat_jid] ?? "other") !== c.chat_role;

  const save = (c: WhatsappTrackedChat) => {
    const next = drafts[c.chat_jid] ?? "other";
    update.mutate(
      { chat_jid: c.chat_jid, chat_role: next },
      {
        onSuccess: () =>
          setBanner({
            kind: "success",
            text: `Set ${c.display_name} → ${CHAT_ROLE_LABEL[next]}.`,
          }),
        onError: (err) =>
          setBanner({
            kind: "error",
            text: `Failed to update: ${err instanceof Error ? err.message : "unknown error"}`,
          }),
      }
    );
  };

  return (
    <div className="bg-background flex h-full flex-col overflow-hidden rounded-lg border">
      <div className="border-b px-4 py-3 sm:px-6">
        <h1 className="text-sm font-semibold tracking-wide uppercase">
          Chat roles
        </h1>
        <p className="text-muted-foreground mt-1 text-xs">
          Tag a tracked WhatsApp chat as <code>tech_dispatch</code> to make
          the ingest pipeline treat operator messages inside it as job
          dispatches. Other roles keep the chat on the customer-facing
          mirror + classify path.
        </p>
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

      <div className="flex-1 overflow-auto">
        {isLoading ? (
          <div className="space-y-2 p-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : isError ? (
          <div className="text-muted-foreground flex flex-col items-center gap-2 p-8 text-center text-xs">
            <p>Failed to load tracked chats.</p>
            <Button variant="outline" size="sm" onClick={() => void refetch()}>
              Retry
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <p className="text-muted-foreground p-8 text-center text-xs">
            No tracked chats yet. Add some via the WhatsApp extension.
          </p>
        ) : (
          <table className="w-full text-sm">
            <thead className="bg-muted/50 sticky top-0 z-10">
              <tr>
                <th className="px-3 py-2 text-left font-medium">Chat</th>
                <th className="px-3 py-2 text-left font-medium">JID</th>
                <th className="px-3 py-2 text-left font-medium">Active</th>
                <th className="px-3 py-2 text-left font-medium">Role</th>
                <th className="px-3 py-2 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((c) => {
                const selected = drafts[c.chat_jid] ?? "other";
                const isDirty = dirty(c);
                const isSaving = update.isPending && update.variables?.chat_jid === c.chat_jid;
                return (
                  <tr key={c.chat_jid} className="border-b">
                    <td className="px-3 py-2">{c.display_name}</td>
                    <td className="text-muted-foreground px-3 py-2 font-mono text-[11px]">
                      {c.chat_jid}
                    </td>
                    <td className="px-3 py-2 text-xs">
                      {c.is_active ? "Yes" : "No"}
                    </td>
                    <td className="px-3 py-2">
                      <select
                        value={selected}
                        onChange={(e) =>
                          setDrafts((d) => ({
                            ...d,
                            [c.chat_jid]: e.target.value as ChatRole,
                          }))
                        }
                        disabled={isSaving}
                        className="border-input bg-background h-8 rounded-md border px-2 text-xs"
                      >
                        {CHAT_ROLES.map((r) => (
                          <option key={r} value={r}>
                            {CHAT_ROLE_LABEL[r]}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="px-3 py-2 text-right">
                      <Button
                        variant={isDirty ? "default" : "outline"}
                        size="sm"
                        onClick={() => save(c)}
                        disabled={!isDirty || isSaving}
                        className="h-7 text-xs"
                      >
                        <Save className="h-3.5 w-3.5" />
                        {isSaving ? "Saving…" : isDirty ? "Save" : "Saved"}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}