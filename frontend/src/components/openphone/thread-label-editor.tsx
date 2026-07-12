"use client";

import { useState } from "react";
import { Loader2, X } from "lucide-react";
import { useCompanies, useSetOpenPhoneThreadLabel, useClearOpenPhoneThreadLabel } from "@/hooks";
import { Button, Input } from "@/components/ui";

interface ThreadLabelEditorProps {
  counterparty: string;
  initialCompanyId: string | null;
  initialLabel: string | null;
  onClose: () => void;
}

export function ThreadLabelEditor({
  counterparty,
  initialCompanyId,
  initialLabel,
  onClose,
}: ThreadLabelEditorProps) {
  const { data: companies } = useCompanies();
  const setLabel = useSetOpenPhoneThreadLabel();
  const clearLabel = useClearOpenPhoneThreadLabel();

  const [companyId, setCompanyId] = useState(initialCompanyId ?? "");
  const [label, setLabelText] = useState(initialLabel ?? "");

  const hasExisting = Boolean(initialCompanyId || initialLabel);
  const saving = setLabel.isPending || clearLabel.isPending;
  const canSave = Boolean(companyId || label.trim());

  const handleSave = async () => {
    const trimmedLabel = label.trim();
    if (!companyId && !trimmedLabel) return;
    await setLabel.mutateAsync({
      counterparty,
      company_id: companyId || null,
      label: trimmedLabel || null,
    });
    onClose();
  };

  const handleClear = async () => {
    await clearLabel.mutateAsync(counterparty);
    onClose();
  };

  return (
    <div className="bg-muted/30 border-b px-4 py-3">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-end">
        <div className="flex-1">
          <label className="text-muted-foreground mb-1 block text-xs" htmlFor="thread-label-company">
            Company
          </label>
          <select
            id="thread-label-company"
            value={companyId}
            onChange={(e) => setCompanyId(e.target.value)}
            className="border-input bg-background h-8 w-full rounded-md border px-2 text-sm"
          >
            <option value="">— none —</option>
            {companies?.items.map((c) => (
              <option key={c.id} value={c.id}>
                {c.display_name || c.name}
              </option>
            ))}
          </select>
        </div>
        <div className="flex-1">
          <label className="text-muted-foreground mb-1 block text-xs" htmlFor="thread-label-text">
            Label
          </label>
          <Input
            id="thread-label-text"
            value={label}
            onChange={(e) => setLabelText(e.target.value)}
            placeholder="e.g. Mike's cell"
            className="h-8"
          />
        </div>
        <div className="flex gap-2">
          <Button type="button" size="sm" onClick={handleSave} disabled={!canSave || saving}>
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save"}
          </Button>
          {hasExisting ? (
            <Button type="button" size="sm" variant="outline" onClick={handleClear} disabled={saving}>
              Clear
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={onClose}
            disabled={saving}
            aria-label="Cancel"
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
