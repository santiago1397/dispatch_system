"use client";

import {
  ChatContainer,
  ConversationSidebar,
  LocalConversationSidebar,
  ChatSidebarToggle,
} from "@/components/chat";
import { useAuthStore } from "@/stores";

export default function ChatPage() {
  const { isAuthenticated } = useAuthStore();

  const Sidebar = isAuthenticated ? ConversationSidebar : LocalConversationSidebar;

  return (
    <div className="-m-3 flex h-full sm:-m-6">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <div className="flex items-center gap-2 border-b p-2 md:hidden">
          <ChatSidebarToggle />
          <span className="text-sm font-medium">Chat</span>
        </div>
        <div className="min-h-0 flex-1">
          <ChatContainer />
        </div>
      </div>
    </div>
  );
}
