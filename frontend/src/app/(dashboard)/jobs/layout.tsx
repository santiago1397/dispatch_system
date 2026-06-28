import { Suspense } from "react";
import { JobsList, JobsToolbar } from "@/components/jobs";

export const metadata = {
  title: "Jobs · Dispatch",
};

/**
 * Two-pane shell for the Jobs section.
 *
 * The list (toolbar + table + pagination) lives in this layout and stays
 * mounted across navigations between `/jobs` and `/jobs/[id]`, so selecting a
 * row never re-fetches the list or loses scroll position. The right pane is
 * `children`, which resolves to either `JobsEmptyDetail` (no selection) or
 * `JobsDetail` (a job is selected).
 *
 * On mobile the panes stack vertically; on `md+` they split 5/7.
 */
export default function JobsLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-background flex h-full min-h-0 flex-col overflow-hidden rounded-lg border">
      <Suspense fallback={null}>
        <div className="grid min-h-0 flex-1 grid-cols-1 md:grid-cols-12">
          {/* Left: filters + list */}
          <section className="flex min-h-0 flex-col border-b md:col-span-5 md:border-r md:border-b-0">
            <JobsToolbar />
            <div className="min-h-0 flex-1 overflow-hidden">
              <JobsList />
            </div>
          </section>

          {/* Right: detail or empty-state */}
          <section className="min-h-0 md:col-span-7">{children}</section>
        </div>
      </Suspense>
    </div>
  );
}
