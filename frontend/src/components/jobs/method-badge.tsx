"use client";

import { cn } from "@/lib/utils";
import {
  DispatchJobMethod,
  METHOD_LABEL,
} from "@/types";

interface MethodBadgeProps {
  method: DispatchJobMethod | null;
  className?: string;
}

const METHOD_STYLES: Record<DispatchJobMethod, string> = {
  phone: "bg-purple-50 text-purple-700 dark:bg-purple-900/30 dark:text-purple-200",
  regex: "bg-cyan-50 text-cyan-700 dark:bg-cyan-900/30 dark:text-cyan-200",
  ai: "bg-indigo-50 text-indigo-700 dark:bg-indigo-900/30 dark:text-indigo-200",
  dedup: "bg-orange-50 text-orange-700 dark:bg-orange-900/30 dark:text-orange-200",
  closing: "bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-200",
};

export function MethodBadge({ method, className }: MethodBadgeProps) {
  if (!method) {
    return (
      <span
        className={cn(
          "text-muted-foreground inline-flex items-center rounded-full px-2 py-0.5 text-xs",
          className
        )}
        aria-label="Method: not yet classified"
      >
        —
      </span>
    );
  }
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
        METHOD_STYLES[method],
        className
      )}
      aria-label={`Method: ${METHOD_LABEL[method]}`}
    >
      {METHOD_LABEL[method]}
    </span>
  );
}
