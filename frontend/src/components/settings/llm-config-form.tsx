"use client";

import { useEffect, useState } from "react";
import { Button, Card, Input, Label, Badge } from "@/components/ui";
import {
  useLLMConfig,
  useUpdateLLMConfig,
  useResetLLMConfig,
} from "@/hooks";
import { Key, Link2, AlertCircle, Check, RotateCcw } from "lucide-react";

function SourceBadge({ source }: { source: "db" | "env" }) {
  return (
    <Badge variant={source === "db" ? "secondary" : "outline"}>
      {source === "db" ? "Override (DB)" : "Fallback (.env)"}
    </Badge>
  );
}

export function LLMConfigForm() {
  const { data, isLoading, error } = useLLMConfig();
  const update = useUpdateLLMConfig();
  const reset = useResetLLMConfig();

  const [apiKey, setApiKey] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [feedback, setFeedback] = useState<string | null>(null);

  // Hydrate base URL from server when it loads. API key stays blank (masked).
  useEffect(() => {
    if (data) setBaseUrl(data.llm_base_url.value);
  }, [data]);

  if (isLoading) {
    return (
      <Card className="p-6">
        <p className="text-muted-foreground text-sm">Loading…</p>
      </Card>
    );
  }

  if (error) {
    return (
      <Card className="border-destructive/50 p-6">
        <div className="text-destructive flex items-center gap-2 text-sm">
          <AlertCircle className="h-4 w-4" />
          {error.status === 403
            ? "Admin access required to view LLM settings."
            : `Failed to load settings: ${error.message}`}
        </div>
      </Card>
    );
  }

  if (!data) return null;

  const apiKeyPlaceholder = data.llm_api_key.is_set
    ? `•••• ${data.llm_api_key.last4 ?? ""}`
    : "Using .env fallback (not set)";

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFeedback(null);
    const payload: { llm_api_key?: string; llm_base_url?: string } = {};
    if (apiKey.trim()) payload.llm_api_key = apiKey.trim();
    if (data && baseUrl.trim() && baseUrl.trim() !== data.llm_base_url.value) {
      payload.llm_base_url = baseUrl.trim();
    }
    if (Object.keys(payload).length === 0) {
      setFeedback("Nothing to update.");
      return;
    }
    try {
      await update.mutateAsync(payload);
      setApiKey("");
      setFeedback("Saved.");
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "Save failed.");
    }
  }

  async function handleReset() {
    if (
      !window.confirm(
        "Clear DB overrides and fall back to the .env values for both fields?"
      )
    ) {
      return;
    }
    setFeedback(null);
    try {
      await reset.mutateAsync();
      setApiKey("");
      setFeedback("Reset to .env defaults.");
    } catch (err) {
      setFeedback(err instanceof Error ? err.message : "Reset failed.");
    }
  }

  return (
    <Card className="p-4 sm:p-6">
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          <h3 className="text-base font-semibold sm:text-lg">LLM configuration</h3>
          <p className="text-muted-foreground text-xs sm:text-sm">
            Overrides the API key and base URL used for company classification
            and field extraction. Empty fields fall back to <code>.env</code>.
          </p>
        </div>
      </div>

      <form onSubmit={handleSubmit} className="grid gap-5">
        <div className="grid gap-2">
          <Label htmlFor="llm-api-key" className="flex items-center gap-2 text-sm">
            <Key className="text-muted-foreground h-4 w-4" />
            API key
            <SourceBadge source={data.llm_api_key.source} />
          </Label>
          <Input
            id="llm-api-key"
            type="password"
            autoComplete="off"
            placeholder={apiKeyPlaceholder}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          <p className="text-muted-foreground text-xs">
            Leave blank to keep the current value. Submitting a new value
            overwrites the DB override.
          </p>
        </div>

        <div className="grid gap-2">
          <Label htmlFor="llm-base-url" className="flex items-center gap-2 text-sm">
            <Link2 className="text-muted-foreground h-4 w-4" />
            Base URL
            <SourceBadge source={data.llm_base_url.source} />
          </Label>
          <Input
            id="llm-base-url"
            type="url"
            placeholder="https://api.openai.com/v1"
            value={baseUrl}
            onChange={(e) => setBaseUrl(e.target.value)}
          />
        </div>

        {feedback && (
          <div className="text-muted-foreground flex items-center gap-2 text-sm">
            <Check className="h-4 w-4" />
            {feedback}
          </div>
        )}

        <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <Button
            type="button"
            variant="outline"
            onClick={handleReset}
            disabled={reset.isPending}
            className="h-10"
          >
            <RotateCcw className="mr-2 h-4 w-4" />
            Reset to .env defaults
          </Button>
          <Button type="submit" disabled={update.isPending} className="h-10">
            {update.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </form>
    </Card>
  );
}
