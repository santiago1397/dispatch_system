"use client";

import { AlertCircle } from "lucide-react";
import { Button } from "@/components/ui";

interface JobsListErrorProps {
  onRetry: () => void;
}

export function JobsListError({ onRetry }: JobsListErrorProps) {
  return (
    <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-3 p-8 text-center">
      <AlertCircle className="text-destructive h-10 w-10" />
      <div>
        <p className="text-foreground text-sm font-medium">Failed to load jobs</p>
        <p className="mt-1 text-xs">The backend returned an error. Check the server logs.</p>
      </div>
      <Button variant="outline" size="sm" onClick={onRetry}>
        Retry
      </Button>
    </div>
  );
}
