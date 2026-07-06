"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Copy, Inbox, Link2, RefreshCw } from "lucide-react";
import type { CompanyUpdate } from "@/types";
import { Button } from "@/components/ui";
import { Skeleton } from "@/components/ui";
import { formatDateTime } from "@/lib/utils";
import {
  useDispatchJob,
  useIncomingMessage,
  useReclassifyJob,
  useRematchClosing,
} from "@/hooks";
import { METHOD_LABEL, SOURCE_LABEL, STATUS_LABEL } from "@/types";
import { MethodBadge } from "./method-badge";
import { StatusBadge } from "./status-badge";
import { LifecycleStatusBadge } from "./lifecycle-status-badge";
import { LifecycleDropdown } from "./lifecycle-dropdown";
import { LifecycleTimeline } from "./lifecycle-timeline";

/** Friendly labels for the tech-reply reason codes (canceled / follow-up). */
const REASON_LABEL: Record<string, string> = {
  refused: "Customer refused",
  dns: "Did not need service",
  solved: "Customer solved it",
  no_service: "No service needed",
  priceshopping: "Price shopping",
  will_cb: "Customer will call back",
  callback: "Wants a callback",
};

/** One key-value row in a grouped section. */
function Field({ label, value }: { label: string; value: string | null | undefined }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="text-sm break-words">{value && value.length > 0 ? value : "—"}</dd>
    </div>
  );
}

/**
 * Detail pane for the Jobs page.
 *
 * Layout (top → bottom):
 *  1. Header — status badge, method badge, company, timestamp, Reclassify button
 *  2. Inline status banner (success/failure of the most recent reclassify)
 *  3. Job section (address, job_type)
 *  4. Customer section (name, phone)
 *  5. Vehicle section (only if any of car_make/model/year is set)
 *  6. Schedule & cost section (scheduled_at, total, parts, payment_method, tech_name)
 *  7. Job description (full width)
 *  8. Original message (source + from_number + created_at metadata + body in fixed-height scroll)
 */
export function JobsDetail() {
  const params = useParams<{ id?: string }>();
  const router = useRouter();
  const id = params?.id ?? null;

  const { data: job, isLoading, isError, refetch } = useDispatchJob(id);
  const { data: message, isLoading: messageLoading } = useIncomingMessage(
    id && job ? job.incoming_message_id : null
  );

  const reclassify = useReclassifyJob(id ?? "");
  const rematchClosing = useRematchClosing(id ?? "");
  const [statusBanner, setStatusBanner] = useState<
    { kind: "success" | "error"; text: string } | null
  >(null);

  // Auto-dismiss the banner after 3s.
  useEffect(() => {
    if (!statusBanner) return;
    const t = setTimeout(() => setStatusBanner(null), 3000);
    return () => clearTimeout(t);
  }, [statusBanner]);

  // 404 — the route returns 404 when the id doesn't exist. The query
  // surfaces that as an error. We can't easily distinguish 404 from a
  // 500 here, so we show a generic "not found" with a link back.
  if (isError) {
    return (
      <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
        <Inbox className="h-10 w-10 opacity-50" />
        <div>
          <p className="text-foreground text-sm font-medium">Job not found</p>
          <p className="mt-1 text-xs">The dispatch job you tried to open doesn't exist.</p>
        </div>
        <Button variant="outline" size="sm" onClick={() => router.push("/jobs")}>
          Back to all jobs
        </Button>
      </div>
    );
  }

  if (isLoading || !job) {
    return <JobsDetailSkeleton />;
  }

  const hasVehicle = Boolean(job.car_make || job.car_model || job.car_year);
  const hasLifecycleDetails = Boolean(job.appt_at || job.follow_up_at || job.reason);
  const isClosingFlow =
    job.classification_status === "closed" ||
    job.classification_status === "closing_unmatched";
  const hasClosingData = Boolean(
    job.total ||
      job.parts ||
      job.payment_method ||
      job.closing_tip ||
      job.closing_notes
  );

  const onReclassify = () => {
    setStatusBanner(null);
    reclassify.mutate(undefined, {
      onSuccess: (data) => {
        setStatusBanner({
          kind: "success",
          text: `Reclassified → ${STATUS_LABEL[data.classification_status]}${
            data.classification_method ? ` (${METHOD_LABEL[data.classification_method]})` : ""
          }`,
        });
      },
      onError: (err) => {
        setStatusBanner({
          kind: "error",
          text: `Reclassification failed: ${err instanceof Error ? err.message : "unknown error"}`,
        });
      },
    });
  };

  const onRematchClosing = () => {
    setStatusBanner(null);
    rematchClosing.mutate(undefined, {
      onSuccess: (data) => {
        const matched = data.classification_status === "closed";
        setStatusBanner({
          kind: matched ? "success" : "error",
          text: matched
            ? "Closing matched to original job."
            : "No matching original job found within the 14-day window.",
        });
      },
      onError: (err) => {
        setStatusBanner({
          kind: "error",
          text: `Rematch failed: ${err instanceof Error ? err.message : "unknown error"}`,
        });
      },
    });
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* Header */}
      <div className="border-b px-4 py-3 sm:px-6">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <StatusBadge status={job.classification_status} />
              <MethodBadge method={job.classification_method} />
              <LifecycleStatusBadge status={job.lifecycle_status} />
              <span className="text-muted-foreground text-xs">
                {job.company_name ?? "—"}
              </span>
              <span className="text-muted-foreground text-xs">·</span>
              <span className="text-muted-foreground text-xs">
                {formatDateTime(job.created_at)}
              </span>
            </div>
            {job.classification_error ? (
              <p className="text-destructive mt-1 text-xs">{job.classification_error}</p>
            ) : null}
            <div className="mt-2">
              <LifecycleDropdown
                jobId={job.id}
                current={job.lifecycle_status}
                onResult={(r) => setStatusBanner(r)}
                disabled={reclassify.isPending || rematchClosing.isPending}
              />
            </div>
          </div>
          <div className="flex shrink-0 gap-2">
            {job.classification_status === "closing_unmatched" ? (
              <Button
                variant="outline"
                size="sm"
                onClick={onRematchClosing}
                disabled={rematchClosing.isPending}
              >
                <Link2
                  className={`h-3.5 w-3.5 ${rematchClosing.isPending ? "animate-spin" : ""}`}
                />
                {rematchClosing.isPending ? "Rematching…" : "Rematch"}
              </Button>
            ) : null}
            <Button
              variant="outline"
              size="sm"
              onClick={onReclassify}
              disabled={reclassify.isPending}
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${reclassify.isPending ? "animate-spin" : ""}`}
              />
              {reclassify.isPending ? "Reclassifying…" : "Reclassify"}
            </Button>
          </div>
        </div>
      </div>

      {/* Status banner */}
      {statusBanner ? (
        <div
          className={`px-4 py-2 text-xs sm:px-6 ${
            statusBanner.kind === "success"
              ? "bg-green-50 text-green-800 dark:bg-green-900/30 dark:text-green-200"
              : "bg-red-50 text-red-800 dark:bg-red-900/30 dark:text-red-200"
          }`}
          role="status"
        >
          {statusBanner.text}
        </div>
      ) : null}

      {/* Scrollable body */}
      <div className="flex-1 overflow-auto px-4 py-4 sm:px-6">
        {/* Send to company — the update to relay, when one is pending. */}
        {job.pending_company_update ? (
          <CompanyRelayCard relay={job.pending_company_update} />
        ) : null}

        {/* Job */}
        <Section title="Job">
          <Field label="Address" value={job.address} />
          <Field label="Job type" value={job.job_type} />
        </Section>

        {/* Customer */}
        <Section title="Customer">
          <Field label="Name" value={job.customer_name} />
          <Field label="Phone" value={job.customer_phone} />
        </Section>

        {/* Vehicle — only show if any of the three fields is set. */}
        {hasVehicle ? (
          <Section title="Vehicle">
            <Field label="Make" value={job.car_make} />
            <Field label="Model" value={job.car_model} />
            <Field label="Year" value={job.car_year} />
          </Section>
        ) : null}

        {/* Follow-up & appointment — the tech-update timings the
            operator acts on. Appointment date/time (appt_set), the
            callback reminder time (needs_follow_up), and the reason. */}
        {hasLifecycleDetails ? (
          <Section title="Follow-up & appointment">
            <Field
              label="Appointment"
              value={job.appt_at ? formatDateTime(job.appt_at) : null}
            />
            <Field
              label="Call customer back"
              value={job.follow_up_at ? formatDateTime(job.follow_up_at) : null}
            />
            <Field
              label="Reason"
              value={job.reason ? (REASON_LABEL[job.reason] ?? job.reason) : null}
            />
          </Section>
        ) : null}

        {/* Schedule & cost — hidden on closing rows; we render a
            dedicated Closing section instead so estimates and actuals
            aren't visually conflated. */}
        {!isClosingFlow ? (
          <Section title="Schedule & cost">
            <Field label="Scheduled at" value={job.scheduled_at} />
            <Field label="Total" value={job.total} />
            <Field label="Parts" value={job.parts} />
            <Field label="Payment" value={job.payment_method} />
            <Field label="Tech" value={job.tech_name} />
          </Section>
        ) : null}

        {/* Closing — only on rows from the "Dispatch closing" pipeline.
            ``total/parts/payment_method`` here are the FINAL actuals
            extracted from the closing message (estimates live on the
            original Job's DispatchJob, not this row). */}
        {isClosingFlow && hasClosingData ? (
          <Section title="Closing">
            <Field label="Total" value={job.total} />
            <Field label="Parts" value={job.parts} />
            <Field label="Tip" value={job.closing_tip} />
            <Field label="Payment" value={job.payment_method} />
            {job.closing_notes ? (
              <div className="sm:col-span-2">
                <dt className="text-muted-foreground text-xs">Notes</dt>
                <dd className="text-sm break-words whitespace-pre-wrap">
                  {job.closing_notes}
                </dd>
              </div>
            ) : null}
          </Section>
        ) : null}

        {/* Job description — full width if present */}
        {job.job_description ? (
          <Section title="Job description" fullWidth>
            <p className="text-sm whitespace-pre-wrap">{job.job_description}</p>
          </Section>
        ) : null}

        {/* Original message — the source the AI read. */}
        <Section title="Original message" fullWidth>
          {messageLoading && !message ? (
            <Skeleton className="h-20 w-full" />
          ) : message ? (
            <>
              <div className="text-muted-foreground mb-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs">
                <span className="bg-secondary rounded px-1.5 py-0.5 text-[10px] font-medium uppercase">
                  {SOURCE_LABEL[message.source]}
                </span>
                {message.from_number ? <span>from {message.from_number}</span> : null}
                <span>{formatDateTime(message.created_at)}</span>
              </div>
              <div className="bg-muted/30 max-h-64 overflow-auto rounded-md border p-3 text-sm whitespace-pre-wrap">
                {message.content ?? "(no content)"}
              </div>
            </>
          ) : (
            <p className="text-muted-foreground text-xs">Source message unavailable.</p>
          )}
        </Section>

        {/* Lifecycle timeline — newest-first append-only event log. */}
        <Section title="Lifecycle" fullWidth>
          <LifecycleTimeline jobId={job.id} />
        </Section>
      </div>
    </div>
  );
}

/**
 * "Send to company" card — the composed update (original job + the tech
 * update) the operator forwards to the company. The system never sends it;
 * this is a copy-and-paste helper. A reminder alert fires if it isn't
 * relayed within the SLA (see the alert engine's company_update_unsent).
 */
function CompanyRelayCard({ relay }: { relay: CompanyUpdate }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(relay.message_text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard blocked (insecure context / permissions) — no-op.
    }
  };
  const to = relay.company_phone ?? relay.company_chat_jid ?? "the company";
  const channel = relay.channel === "openphone" ? "OpenPhone" : "WhatsApp";
  return (
    <section className="mb-5 rounded-md border border-amber-300 bg-amber-50 p-3 dark:border-amber-800/60 dark:bg-amber-900/20">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h3 className="text-xs font-semibold tracking-wide text-amber-800 uppercase dark:text-amber-200">
          Send to company
        </h3>
        <Button size="sm" variant="outline" onClick={onCopy}>
          <Copy className="h-3.5 w-3.5" />
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      <p className="mb-2 text-xs text-amber-800/80 dark:text-amber-200/80">
        Forward this update to {to} in {channel}. The system won&apos;t send it for you.
      </p>
      <div className="bg-background max-h-48 overflow-auto rounded border p-2 text-sm whitespace-pre-wrap">
        {relay.message_text}
      </div>
    </section>
  );
}

/** A grouped section with a heading and a 2-column definition list. */
function Section({
  title,
  children,
  fullWidth = false,
}: {
  title: string;
  children: React.ReactNode;
  fullWidth?: boolean;
}) {
  return (
    <section className="mb-5 last:mb-0">
      <h3 className="mb-2 text-xs font-semibold tracking-wide uppercase">{title}</h3>
      {fullWidth ? (
        children
      ) : (
        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">{children}</dl>
      )}
    </section>
  );
}

function JobsDetailSkeleton() {
  return (
    <div className="flex h-full flex-col">
      <div className="border-b px-4 py-3 sm:px-6">
        <div className="flex items-center justify-between">
          <div className="flex gap-2">
            <Skeleton className="h-5 w-20" />
            <Skeleton className="h-5 w-16" />
            <Skeleton className="h-5 w-32" />
          </div>
          <Skeleton className="h-8 w-28" />
        </div>
      </div>
      <div className="flex-1 space-y-5 px-4 py-4 sm:px-6">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i}>
            <Skeleton className="mb-2 h-3 w-20" />
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
