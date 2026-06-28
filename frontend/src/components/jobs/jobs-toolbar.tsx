"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui";
import { useCompanies } from "@/hooks";
import { JOB_FILTER_STATUSES, STATUS_LABEL, type DispatchJobStatus } from "@/types";

/**
 * Filter toolbar for the Jobs list.
 *
 * Three controls: status (dropdown), company (dropdown), date range
 * (two date inputs). All three live in the URL (?status, ?company_id,
 * ?from, ?to). On any change, we reset ?page=1 and close the right
 * pane by removing any [id] segment via the parent (the toolbar itself
 * just pushes /jobs?...).
 *
 * The inputs are uncontrolled — we initialize from the URL once on
 * mount and write back to the URL on change. This avoids the
 * "controlled input + every keystroke = URL change" anti-pattern.
 */
export function JobsToolbar() {
  const router = useRouter();
  const sp = useSearchParams();
  const { data: companiesData } = useCompanies();

  // Read current values from the URL.
  const currentStatus = sp.get("status") as DispatchJobStatus | null;
  const currentCompanyId = sp.get("company_id");
  const currentFrom = sp.get("from");
  const currentTo = sp.get("to");
  const currentQ = sp.get("q");

  // Local "applied" copies that drive the inputs. We sync from the URL
  // when the URL changes (e.g., back/forward, programmatic reset).
  const [status, setStatus] = useState<string>(currentStatus ?? "");
  const [companyId, setCompanyId] = useState<string>(currentCompanyId ?? "");
  const [from, setFrom] = useState<string>(currentFrom ?? "");
  const [to, setTo] = useState<string>(currentTo ?? "");
  const [q, setQ] = useState<string>(currentQ ?? "");

  const companies = companiesData?.items ?? [];

  // The URL is the source of truth — resync local state whenever
  // params change (e.g., sidebar nav back to /jobs, browser back/forward).
  useEffect(() => {
    setStatus(currentStatus ?? "");
    setCompanyId(currentCompanyId ?? "");
    setFrom(currentFrom ?? "");
    setTo(currentTo ?? "");
    setQ(currentQ ?? "");
  }, [currentStatus, currentCompanyId, currentFrom, currentTo, currentQ]);

  const applyFilters = useCallback(
    (next: {
      status?: string;
      company_id?: string;
      from?: string;
      to?: string;
      q?: string;
    }) => {
      const params = new URLSearchParams();
      const merged = {
        status: next.status ?? status,
        company_id: next.company_id ?? companyId,
        from: next.from ?? from,
        to: next.to ?? to,
        q: next.q ?? q,
      };
      if (merged.status) params.set("status", merged.status);
      if (merged.company_id) params.set("company_id", merged.company_id);
      if (merged.from) params.set("from", merged.from);
      if (merged.to) params.set("to", merged.to);
      if (merged.q && merged.q.length >= 2) params.set("q", merged.q);
      // Always reset to page 1 on filter change; drop the [id] segment
      // by navigating to /jobs?… (parent layout will clear the detail).
      const qs = params.toString();
      router.replace(`/jobs${qs ? `?${qs}` : ""}`);
    },
    [router, status, companyId, from, to, q]
  );

  const hasAnyFilter = useMemo(
    () => Boolean(status || companyId || from || to || q),
    [status, companyId, from, to, q]
  );

  const clearFilters = useCallback(() => {
    setStatus("");
    setCompanyId("");
    setFrom("");
    setTo("");
    setQ("");
    router.replace("/jobs");
  }, [router]);

  // Debounce the search input so the URL doesn't churn on every keystroke.
  // Compared to ?q in the URL — only push when the typed value diverges.
  const qDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (qDebounceRef.current) clearTimeout(qDebounceRef.current);
    const trimmed = q.trim();
    const urlQ = currentQ ?? "";
    const nextQ = trimmed.length >= 2 ? trimmed : "";
    if (nextQ === urlQ) return;
    qDebounceRef.current = setTimeout(() => {
      applyFilters({ q: nextQ });
    }, 300);
    return () => {
      if (qDebounceRef.current) clearTimeout(qDebounceRef.current);
    };
  }, [q, currentQ, applyFilters]);

  return (
    <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2 sm:px-4">
      <input
        type="search"
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="Search message…"
        aria-label="Search raw message text"
        className="border-input bg-background h-8 min-w-[180px] flex-1 rounded-md border px-2 text-sm"
      />

      <select
        value={status}
        onChange={(e) => {
          setStatus(e.target.value);
          applyFilters({ status: e.target.value });
        }}
        aria-label="Filter by status"
        className="border-input bg-background h-8 rounded-md border px-2 text-sm"
      >
        <option value="">All statuses</option>
        {JOB_FILTER_STATUSES.map((s) => (
          <option key={s} value={s}>
            {STATUS_LABEL[s]}
          </option>
        ))}
      </select>

      <select
        value={companyId}
        onChange={(e) => {
          setCompanyId(e.target.value);
          applyFilters({ company_id: e.target.value });
        }}
        aria-label="Filter by company"
        className="border-input bg-background h-8 max-w-[180px] rounded-md border px-2 text-sm"
        disabled={companies.length === 0}
      >
        <option value="">All companies</option>
        {companies.map((c) => (
          <option key={c.id} value={c.id}>
            {c.display_name ?? c.name}
          </option>
        ))}
      </select>

      <label className="text-muted-foreground flex items-center gap-1 text-xs">
        From
        <input
          type="date"
          value={from}
          onChange={(e) => {
            setFrom(e.target.value);
            applyFilters({ from: e.target.value });
          }}
          aria-label="Filter from date"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm"
        />
      </label>

      <label className="text-muted-foreground flex items-center gap-1 text-xs">
        To
        <input
          type="date"
          value={to}
          onChange={(e) => {
            setTo(e.target.value);
            applyFilters({ to: e.target.value });
          }}
          aria-label="Filter to date"
          className="border-input bg-background h-8 rounded-md border px-2 text-sm"
        />
      </label>

      {hasAnyFilter ? (
        <Button variant="ghost" size="sm" onClick={clearFilters} className="h-8 px-2 text-xs">
          Clear
        </Button>
      ) : null}
    </div>
  );
}
