"use client";

import { Inbox } from "lucide-react";

export function JobsListEmpty() {
  return (
    <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <Inbox className="h-10 w-10 opacity-50" />
      <div>
        <p className="text-foreground text-sm font-medium">No jobs yet</p>
        <p className="mt-1 text-xs">
          Open WhatsApp Web with the extension connected, or wait for an
          OpenPhone webhook.
        </p>
      </div>
    </div>
  );
}
