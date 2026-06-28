"use client";

import { useState } from "react";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Skeleton,
} from "@/components/ui";
import { useCompanies, type Company } from "@/hooks/use-companies";
import {
  useCreatePhoneBinding,
  useDeletePhoneBinding,
  usePhoneBindings,
  usePhoneBindingSuggestions,
  type PhoneBindingSuggestion,
} from "@/hooks/use-phone-bindings";

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function companyLabel(c: Pick<Company, "display_name" | "name">): string {
  return c.display_name || c.name;
}

export default function PhoneBindingsPage() {
  const bindings = usePhoneBindings();
  const suggestions = usePhoneBindingSuggestions();
  const companies = useCompanies();
  const create = useCreatePhoneBinding();
  const remove = useDeletePhoneBinding();

  const [manualPhone, setManualPhone] = useState("");
  const [manualCompany, setManualCompany] = useState("");
  const [manualNote, setManualNote] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [rowError, setRowError] = useState<string | null>(null);

  const companyOptions = companies.data?.items ?? [];

  async function handleManualCreate(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!manualPhone.trim() || !manualCompany) {
      setFormError("Phone and company are required");
      return;
    }
    try {
      await create.mutateAsync({
        phone: manualPhone.trim(),
        company_id: manualCompany,
        note: manualNote.trim() || null,
      });
      setManualPhone("");
      setManualCompany("");
      setManualNote("");
    } catch (err) {
      const detail = (err as { message?: string })?.message ?? "Failed to create";
      setFormError(detail);
    }
  }

  async function bindSuggestion(s: PhoneBindingSuggestion, companyId: string) {
    setRowError(null);
    try {
      await create.mutateAsync({
        phone: s.phone_e164,
        company_id: companyId,
        note: `Auto-suggested (${s.hits} regex hits)`,
      });
    } catch (err) {
      const detail = (err as { message?: string })?.message ?? "Failed to bind";
      setRowError(`${s.from_number}: ${detail}`);
    }
  }

  async function handleDelete(id: string) {
    setRowError(null);
    try {
      await remove.mutateAsync(id);
    } catch (err) {
      const detail = (err as { message?: string })?.message ?? "Failed to delete";
      setRowError(detail);
    }
  }

  return (
    <div className="container mx-auto max-w-5xl">
      <div className="mb-6 sm:mb-8">
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">
          Phone bindings
        </h1>
        <p className="text-muted-foreground text-sm sm:text-base">
          Operator-curated map from OpenPhone sender numbers to companies.
          Used by the classifier as a fallback when body regex finds nothing.
          Regex always wins on conflict.
        </p>
      </div>

      <div className="grid gap-4 sm:gap-6">
        {/* === Suggestions === */}
        <Card>
          <CardHeader>
            <CardTitle>Suggestions</CardTitle>
            <CardDescription>
              Numbers observed to regex-classify to a company. Click Bind to
              accept; override the company first if needed.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {suggestions.isLoading ? (
              <SkeletonRows />
            ) : suggestions.data?.items.length === 0 ? (
              <p className="text-muted-foreground text-sm">
                No new suggestions — every regex-classified number is already
                bound.
              </p>
            ) : (
              <SuggestionsTable
                rows={suggestions.data?.items ?? []}
                companies={companyOptions}
                pending={create.isPending}
                onBind={bindSuggestion}
              />
            )}
          </CardContent>
        </Card>

        {/* === Current bindings === */}
        <Card>
          <CardHeader>
            <CardTitle>Current bindings</CardTitle>
            <CardDescription>
              {bindings.data?.total ?? 0} active binding
              {bindings.data?.total === 1 ? "" : "s"}.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {bindings.isLoading ? (
              <SkeletonRows />
            ) : bindings.data?.items.length === 0 ? (
              <p className="text-muted-foreground text-sm">
                No bindings yet. Accept a suggestion above or add one manually
                below.
              </p>
            ) : (
              <table className="w-full text-sm">
                <thead className="border-b text-muted-foreground text-left">
                  <tr>
                    <th className="py-2 pr-4 font-medium">Phone</th>
                    <th className="py-2 pr-4 font-medium">Company</th>
                    <th className="py-2 pr-4 font-medium">Note</th>
                    <th className="py-2 pr-4 font-medium">Created</th>
                    <th className="py-2 font-medium" />
                  </tr>
                </thead>
                <tbody>
                  {bindings.data!.items.map((b) => (
                    <tr key={b.id} className="border-b last:border-0">
                      <td className="py-2 pr-4 font-mono">{b.phone_e164}</td>
                      <td className="py-2 pr-4">
                        {b.company_display_name || b.company_name}
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {b.note ?? "—"}
                      </td>
                      <td className="py-2 pr-4 text-muted-foreground">
                        {formatDate(b.created_at)}
                      </td>
                      <td className="py-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleDelete(b.id)}
                          disabled={remove.isPending}
                        >
                          Remove
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {rowError && (
              <p className="mt-3 text-sm text-destructive">{rowError}</p>
            )}
          </CardContent>
        </Card>

        {/* === Manual add === */}
        <Card>
          <CardHeader>
            <CardTitle>Add manually</CardTitle>
            <CardDescription>
              Bind a number that hasn&apos;t shown up in suggestions yet.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form onSubmit={handleManualCreate} className="grid gap-4">
              <div className="grid gap-2">
                <Label htmlFor="phone">Phone</Label>
                <Input
                  id="phone"
                  placeholder="+1 470 471 4943"
                  value={manualPhone}
                  onChange={(e) => setManualPhone(e.target.value)}
                  required
                />
              </div>
              <div className="grid gap-2">
                <Label htmlFor="company">Company</Label>
                <select
                  id="company"
                  className="bg-background border-input flex h-10 w-full rounded-md border px-3 py-2 text-sm"
                  value={manualCompany}
                  onChange={(e) => setManualCompany(e.target.value)}
                  required
                >
                  <option value="">Select a company…</option>
                  {companyOptions.map((c) => (
                    <option key={c.id} value={c.id}>
                      {companyLabel(c)}
                    </option>
                  ))}
                </select>
              </div>
              <div className="grid gap-2">
                <Label htmlFor="note">Note (optional)</Label>
                <Input
                  id="note"
                  placeholder="Where this number came from, etc."
                  value={manualNote}
                  onChange={(e) => setManualNote(e.target.value)}
                />
              </div>
              {formError && (
                <p className="text-sm text-destructive">{formError}</p>
              )}
              <div>
                <Button type="submit" disabled={create.isPending}>
                  {create.isPending ? "Adding…" : "Add binding"}
                </Button>
              </div>
            </form>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function SkeletonRows() {
  return (
    <div className="space-y-2">
      <Skeleton className="h-6 w-full" />
      <Skeleton className="h-6 w-full" />
      <Skeleton className="h-6 w-2/3" />
    </div>
  );
}

interface SuggestionsTableProps {
  rows: PhoneBindingSuggestion[];
  companies: Company[];
  pending: boolean;
  onBind: (s: PhoneBindingSuggestion, companyId: string) => void;
}

function SuggestionsTable({
  rows,
  companies,
  pending,
  onBind,
}: SuggestionsTableProps) {
  // Row-local overrides for the company dropdown — defaults to the
  // suggested company; lets the operator re-target before clicking Bind.
  const [overrides, setOverrides] = useState<Record<string, string>>({});

  return (
    <table className="w-full text-sm">
      <thead className="border-b text-muted-foreground text-left">
        <tr>
          <th className="py-2 pr-4 font-medium">Number</th>
          <th className="py-2 pr-4 font-medium">Suggested company</th>
          <th className="py-2 pr-4 font-medium">Hits</th>
          <th className="py-2 pr-4 font-medium">Last seen</th>
          <th className="py-2 font-medium" />
        </tr>
      </thead>
      <tbody>
        {rows.map((s) => {
          const key = `${s.phone_e164}:${s.company_id}`;
          const targetCompany = overrides[key] ?? s.company_id;
          return (
            <tr key={key} className="border-b last:border-0">
              <td className="py-2 pr-4 font-mono">{s.from_number}</td>
              <td className="py-2 pr-4">
                <select
                  className="bg-background border-input rounded-md border px-2 py-1 text-sm"
                  value={targetCompany}
                  onChange={(e) =>
                    setOverrides((o) => ({ ...o, [key]: e.target.value }))
                  }
                >
                  {companies.map((c) => (
                    <option key={c.id} value={c.id}>
                      {companyLabel(c)}
                    </option>
                  ))}
                </select>
              </td>
              <td className="py-2 pr-4">
                <Badge variant="secondary">{s.hits}</Badge>
              </td>
              <td className="py-2 pr-4 text-muted-foreground">
                {formatDate(s.last_seen_at)}
              </td>
              <td className="py-2">
                <Button
                  size="sm"
                  onClick={() => onBind(s, targetCompany)}
                  disabled={pending}
                >
                  Bind
                </Button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
