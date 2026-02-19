"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui";
import type { ToolCall } from "@/types";
import { Wrench, CheckCircle, Loader2, AlertCircle } from "lucide-react";
import { cn } from "@/lib/utils";
import { CopyButton } from "./copy-button";

interface ToolCallCardProps {
  toolCall: ToolCall;
}

export function ToolCallCard({ toolCall }: ToolCallCardProps) {
  const statusConfig = {
    pending: { icon: Loader2, color: "text-muted-foreground", animate: true },
    running: { icon: Loader2, color: "text-blue-500", animate: true },
    completed: { icon: CheckCircle, color: "text-green-500", animate: false },
    error: { icon: AlertCircle, color: "text-red-500", animate: false },
  };

  const { icon: StatusIcon, color, animate } = statusConfig[toolCall.status];

  const argsText = JSON.stringify(toolCall.args, null, 2);
  const resultText =
    toolCall.result !== undefined
      ? typeof toolCall.result === "string"
        ? toolCall.result
        : JSON.stringify(toolCall.result, null, 2)
      : "";

  return (
    <Card className="bg-muted/50">
      <CardHeader className="px-3 py-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Wrench className="text-muted-foreground h-4 w-4" />
            <CardTitle className="text-sm font-medium">{toolCall.name}</CardTitle>
          </div>
          <StatusIcon className={cn("h-4 w-4", color, animate && "animate-spin")} />
        </div>
      </CardHeader>
      <CardContent className="space-y-2 px-3 py-2">
        {/* Arguments */}
        <div className="group relative">
          <div className="mb-1 flex items-center justify-between">
            <p className="text-muted-foreground text-xs">Arguments:</p>
            <CopyButton text={argsText} className="opacity-0 group-hover:opacity-100" />
          </div>
          <pre className="bg-background overflow-x-auto rounded p-2 text-xs">{argsText}</pre>
        </div>

        {/* Result */}
        {toolCall.result !== undefined && (
          <div className="group relative">
            <div className="mb-1 flex items-center justify-between">
              <p className="text-muted-foreground text-xs">Result:</p>
              <CopyButton text={resultText} className="opacity-0 group-hover:opacity-100" />
            </div>
            <pre className="bg-background max-h-48 overflow-x-auto overflow-y-auto rounded p-2 text-xs">
              {resultText}
            </pre>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
