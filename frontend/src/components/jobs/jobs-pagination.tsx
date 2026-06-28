"use client";

import { Button } from "@/components/ui";
import { ChevronLeft, ChevronRight } from "lucide-react";

interface JobsPaginationProps {
  page: number;
  totalPages: number;
  total: number;
  onChange: (page: number) => void;
}

export function JobsPagination({ page, totalPages, total, onChange }: JobsPaginationProps) {
  const canPrev = page > 1;
  const canNext = page < totalPages;
  const first = total === 0 ? 0 : (page - 1) * 50 + 1;
  const last = Math.min(total, page * 50);

  return (
    <div className="text-muted-foreground flex items-center justify-between border-t px-3 py-2 text-xs">
      <span>
        {total === 0
          ? "0 of 0"
          : `${first}–${last} of ${total}`}
      </span>
      <div className="flex items-center gap-1">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onChange(page - 1)}
          disabled={!canPrev}
          className="h-7 w-7 p-0"
          aria-label="Previous page"
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <span className="px-1 tabular-nums">
          {page} / {totalPages}
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => onChange(page + 1)}
          disabled={!canNext}
          className="h-7 w-7 p-0"
          aria-label="Next page"
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}
