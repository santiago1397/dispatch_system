"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import { useSetLifecycleStatus } from "@/hooks";
import {
  LIFECYCLE_STATUS_LABEL,
  MANUAL_LIFECYCLE_TRANSITIONS,
  type LifecycleStatus,
} from "@/types";

interface LifecycleDropdownProps {
  jobId: string;
  /**
   * Current lifecycle status of the Job. May be null on legacy rows;
   * we treat null as "pending" in the dropdown.
   */
  current: LifecycleStatus | null;
  /** Callback fired with the latest mutation error text (for inline banner). */
  onResult?: (result: { kind: "success" | "error"; text: string } | null) => void;
  /** Disables interaction while a parent mutation is in flight. */
  disabled?: boolean;
}

/**
 * Manual override dropdown for the lifecycle status.
 *
 * Rules enforced client-side (the backend enforces them too):
 *  - ``closed`` is excluded — closing flows only through the
 *    ``CLOSING_CHAT_JID`` WhatsApp group, never from this dropdown.
 *  - ``canceled`` requires a non-empty note. We reveal a textarea inline
 *    when the operator picks "Canceled".
 */
export function LifecycleDropdown({
  jobId,
  current,
  onResult,
  disabled,
}: LifecycleDropdownProps) {
  const [pendingTo, setPendingTo] = useState<LifecycleStatus | null>(null);
  const [note, setNote] = useState("");
  const mutation = useSetLifecycleStatus(jobId);

  const currentValue = current ?? "pending";

  const submit = (to: LifecycleStatus, noteText?: string | null) => {
    setPendingTo(null);
    setNote("");
    mutation.mutate(
      { to_status: to, note: noteText ?? null },
      {
        onSuccess: (job) => {
          onResult?.({
            kind: "success",
            text: `Lifecycle set to ${LIFECYCLE_STATUS_LABEL[job.lifecycle_status ?? "pending"]}.`,
          });
        },
        onError: (err) => {
          onResult?.({
            kind: "error",
            text: `Failed to set lifecycle: ${err instanceof Error ? err.message : "unknown error"}`,
          });
        },
      }
    );
  };

  const onSelect = (next: LifecycleStatus) => {
    if (next === currentValue) return;
    if (next === "canceled") {
      // Reveal the note input; the operator must type something before
      // we submit. The Confirm button calls submit() with the note.
      setPendingTo(next);
      return;
    }
    submit(next);
  };

  const onCancelNote = () => {
    setPendingTo(null);
    setNote("");
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <label
          htmlFor="lifecycle-select"
          className="text-muted-foreground text-xs"
        >
          Lifecycle:
        </label>
        <select
          id="lifecycle-select"
          value={pendingTo ?? currentValue}
          onChange={(e) => onSelect(e.target.value as LifecycleStatus)}
          disabled={disabled || mutation.isPending}
          className="border-input bg-background h-7 rounded-md border px-2 text-xs"
        >
          {MANUAL_LIFECYCLE_TRANSITIONS.map((s) => (
            <option key={s} value={s}>
              {LIFECYCLE_STATUS_LABEL[s]}
            </option>
          ))}
        </select>
        {mutation.isPending ? (
          <span className="text-muted-foreground text-[10px]">Saving…</span>
        ) : null}
      </div>

      {pendingTo === "canceled" ? (
        <div className="bg-muted/40 space-y-2 rounded-md border p-2">
          <label
            htmlFor="cancel-note"
            className="text-muted-foreground block text-[10px] tracking-wide uppercase"
          >
            Cancellation note (required)
          </label>
          <textarea
            id="cancel-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            placeholder="Why is this job being canceled?"
            className="border-input bg-background w-full rounded-md border p-2 text-xs"
          />
          <div className="flex justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={onCancelNote}
              disabled={mutation.isPending}
              className="h-7 text-xs"
            >
              Cancel
            </Button>
            <Button
              variant="default"
              size="sm"
              onClick={() => submit("canceled", note.trim())}
              disabled={mutation.isPending || note.trim().length === 0}
              className="h-7 text-xs"
            >
              Confirm cancel
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}