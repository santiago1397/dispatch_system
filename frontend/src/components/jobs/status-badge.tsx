"use client";

import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";
import {
  DispatchJobStatus,
  STATUS_LABEL,
} from "@/types";

const statusBadgeVariants = cva(
  "inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium",
  {
    variants: {
      status: {
        classified: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-200",
        linked: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200",
        pending: "bg-yellow-100 text-yellow-800 dark:bg-yellow-900/40 dark:text-yellow-200",
        failed: "bg-red-100 text-red-800 dark:bg-red-900/40 dark:text-red-200",
        not_a_job: "bg-gray-100 text-gray-700 dark:bg-gray-800/60 dark:text-gray-300",
        closed: "bg-emerald-100 text-emerald-900 dark:bg-emerald-900/40 dark:text-emerald-100",
        closing_unmatched: "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-100",
      },
    },
    defaultVariants: {
      status: "pending",
    },
  }
);

interface StatusBadgeProps
  extends Omit<React.HTMLAttributes<HTMLSpanElement>, "children">,
    VariantProps<typeof statusBadgeVariants> {
  status: DispatchJobStatus;
}

export function StatusBadge({ status, className, ...props }: StatusBadgeProps) {
  return (
    <span
      className={cn(statusBadgeVariants({ status }), className)}
      aria-label={`Status: ${STATUS_LABEL[status]}`}
      {...props}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}
