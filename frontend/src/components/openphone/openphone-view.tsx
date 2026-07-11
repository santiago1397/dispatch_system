"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { ThreadList } from "./thread-list";
import { MessageList } from "./message-list";
import { EmptyState } from "@/components/whatsapp/empty-state";

export function OpenPhoneView() {
  const router = useRouter();
  const sp = useSearchParams();
  const activeCounterparty = sp.get("thread");

  const handleSelect = (counterparty: string) => {
    const params = new URLSearchParams(sp.toString());
    params.set("thread", counterparty);
    router.replace(`/openphone?${params.toString()}`);
  };

  return (
    <div className="-m-3 flex h-[calc(100vh-3.5rem)] sm:-m-6 sm:h-[calc(100vh-3.5rem)]">
      <aside className="bg-background hidden w-72 shrink-0 flex-col border-r md:flex">
        <div className="flex h-12 items-center border-b px-4">
          <h2 className="text-sm font-semibold">OpenPhone threads</h2>
        </div>
        <ThreadList activeCounterparty={activeCounterparty} onSelect={handleSelect} />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        {activeCounterparty ? (
          <div className="min-h-0 flex-1">
            <MessageList key={activeCounterparty} counterparty={activeCounterparty} />
          </div>
        ) : (
          <EmptyState
            title="Select a thread"
            description="Pick a conversation from the list to see its OpenPhone messages."
          />
        )}
      </div>
    </div>
  );
}
