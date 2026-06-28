"use client";

import { Skeleton } from "@/components/ui/skeleton";
// Skeleton isn't in the existing UI set; we'll add a minimal one.

export function JobsListSkeleton() {
  return (
    <div className="flex h-full flex-col">
      <div className="flex-1 space-y-1 p-3">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="flex items-center gap-2 py-2">
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-4 w-16" />
            <Skeleton className="h-4 w-12" />
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-4 w-24" />
          </div>
        ))}
      </div>
    </div>
  );
}
