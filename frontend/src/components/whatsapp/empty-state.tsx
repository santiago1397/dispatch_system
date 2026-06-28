"use client";

import { MessageCircle } from "lucide-react";

interface EmptyStateProps {
  title: string;
  description?: string;
}

export function EmptyState({ title, description }: EmptyStateProps) {
  return (
    <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 p-8 text-center text-sm">
      <MessageCircle className="h-8 w-8 opacity-50" />
      <p className="font-medium text-foreground">{title}</p>
      {description ? <p className="max-w-sm text-xs">{description}</p> : null}
    </div>
  );
}
