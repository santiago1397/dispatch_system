"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import { useCompanies, useSetJobCompany } from "@/hooks";

interface CompanyReassignControlProps {
  jobId: string;
  currentCompanyId: string | null;
  currentCompanyName: string | null;
  /** Callback fired with the latest mutation result (for inline banner). */
  onResult?: (result: { kind: "success" | "error"; text: string } | null) => void;
  /** Disables interaction while a parent mutation is in flight. */
  disabled?: boolean;
}

/** Sentinel option value meaning "detach from any company". */
const DETACH_VALUE = "__detach__";

/**
 * Manual company reassign/detach control, for correcting misclassifications
 * that inflate the wrong company's report (e.g. a message regex-matched via
 * a shared broker phone number, or a company that doesn't exist in the
 * system yet). Unlike the Lifecycle dropdown's "Rejected"/"Canceled", this
 * removes the job from every company's report entirely — it isn't a status,
 * it's an attribution fix. The job row itself is never deleted.
 *
 * A note is always required, mirroring the lifecycle dropdown's cancellation
 * note, since this overrides the classifier's decision.
 */
export function CompanyReassignControl({
  jobId,
  currentCompanyId,
  currentCompanyName,
  onResult,
  disabled,
}: CompanyReassignControlProps) {
  const { data: companiesData } = useCompanies();
  const [pendingValue, setPendingValue] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const mutation = useSetJobCompany(jobId);

  const currentValue = currentCompanyId ?? "";
  const companies = companiesData?.items ?? [];

  const onSelect = (value: string) => {
    if (value === currentValue) return;
    setPendingValue(value);
  };

  const onCancel = () => {
    setPendingValue(null);
    setNote("");
  };

  const submit = () => {
    const trimmed = note.trim();
    if (!trimmed || pendingValue === null) return;
    const company_id = pendingValue === DETACH_VALUE ? null : pendingValue;
    mutation.mutate(
      { company_id, note: trimmed },
      {
        onSuccess: (job) => {
          setPendingValue(null);
          setNote("");
          onResult?.({
            kind: "success",
            text: job.company_name
              ? `Job reassigned to ${job.company_name}.`
              : "Job detached from any company — it no longer counts toward any report.",
          });
        },
        onError: (err) => {
          onResult?.({
            kind: "error",
            text: `Failed to update company: ${err instanceof Error ? err.message : "unknown error"}`,
          });
        },
      }
    );
  };

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <label htmlFor="company-select" className="text-muted-foreground text-xs">
          Company:
        </label>
        <select
          id="company-select"
          value={pendingValue ?? currentValue}
          onChange={(e) => onSelect(e.target.value)}
          disabled={disabled || mutation.isPending}
          className="border-input bg-background h-7 rounded-md border px-2 text-xs"
        >
          {!currentCompanyId ? <option value="">— none —</option> : null}
          {companies.map((c) => (
            <option key={c.id} value={c.id}>
              {c.display_name ?? c.name}
            </option>
          ))}
          <option value={DETACH_VALUE}>Detach (not a real job for this company)</option>
        </select>
        {mutation.isPending ? (
          <span className="text-muted-foreground text-[10px]">Saving…</span>
        ) : null}
      </div>

      {pendingValue !== null ? (
        <div className="bg-muted/40 space-y-2 rounded-md border p-2">
          <label
            htmlFor="company-reassign-note"
            className="text-muted-foreground block text-[10px] tracking-wide uppercase"
          >
            Reason (required)
          </label>
          <textarea
            id="company-reassign-note"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            placeholder={
              pendingValue === DETACH_VALUE
                ? `Why is this being detached from ${currentCompanyName ?? "its company"}?`
                : "Why is this being reassigned?"
            }
            className="border-input bg-background w-full rounded-md border p-2 text-xs"
          />
          <div className="flex justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={onCancel}
              disabled={mutation.isPending}
              className="h-7 text-xs"
            >
              Cancel
            </Button>
            <Button
              variant="default"
              size="sm"
              onClick={submit}
              disabled={mutation.isPending || note.trim().length === 0}
              className="h-7 text-xs"
            >
              Confirm
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
