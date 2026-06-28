"use client";

import { MousePointerClick } from "lucide-react";

export function JobsEmptyDetail() {
  return (
    <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <MousePointerClick className="h-10 w-10 opacity-50" />
      <div>
        <p className="text-foreground text-sm font-medium">Select a job</p>
        <p className="mt-1 text-xs">
          Pick a dispatch from the list to see the extracted fields and the
          original message.
        </p>
      </div>
    </div>
  );
}
