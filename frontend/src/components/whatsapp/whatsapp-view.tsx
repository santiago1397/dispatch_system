"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { ChatList } from "./chat-list";
import { MessageList } from "./message-list";
import { EmptyState } from "./empty-state";

export function WhatsappView() {
  const router = useRouter();
  const sp = useSearchParams();
  const activeChatJid = sp.get("chat");

  const handleSelect = (chatJid: string) => {
    const params = new URLSearchParams(sp.toString());
    params.set("chat", chatJid);
    router.replace(`/whatsapp?${params.toString()}`);
  };

  return (
    <div className="-m-3 flex h-[calc(100vh-3.5rem)] sm:-m-6 sm:h-[calc(100vh-3.5rem)]">
      <aside className="bg-background hidden w-72 shrink-0 flex-col border-r md:flex">
        <div className="flex h-12 items-center border-b px-4">
          <h2 className="text-sm font-semibold">Tracked chats</h2>
        </div>
        <ChatList activeChatJid={activeChatJid} onSelect={handleSelect} />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        {activeChatJid ? (
          <div className="min-h-0 flex-1">
            <MessageList key={activeChatJid} chatJid={activeChatJid} />
          </div>
        ) : (
          <EmptyState
            title="Select a chat"
            description="Pick a tracked chat from the list to see the scraped messages."
          />
        )}
      </div>
    </div>
  );
}
