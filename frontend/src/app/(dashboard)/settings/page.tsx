"use client";

import Link from "next/link";
import { LLMConfigForm } from "@/components/settings/llm-config-form";
import {
  Card,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui";

export default function SettingsPage() {
  return (
    <div className="container mx-auto max-w-3xl">
      <div className="mb-6 sm:mb-8">
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">Settings</h1>
        <p className="text-muted-foreground text-sm sm:text-base">
          Runtime configuration overrides (admin only).
        </p>
      </div>

      <div className="grid gap-4 sm:gap-6">
        <LLMConfigForm />

        <Link href="/settings/phone-bindings" className="block">
          <Card className="hover:bg-secondary/30 transition-colors">
            <CardHeader>
              <CardTitle>Phone bindings</CardTitle>
              <CardDescription>
                Map OpenPhone sender numbers to companies for the
                classifier&apos;s fallback tier.
              </CardDescription>
            </CardHeader>
          </Card>
        </Link>
      </div>
    </div>
  );
}
