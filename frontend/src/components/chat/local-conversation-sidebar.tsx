"use client";

import { useState } from "react";
import { Button } from "@/components/ui";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetClose } from "@/components/ui";
import { cn } from "@/lib/utils";
import { useLocalChatStore, useChatSidebarStore } from "@/stores";
import type { LocalConversation } from "@/stores/local-chat-store";
import {
  MessageSquarePlus,
  MessageSquare,
  Trash2,
  MoreVertical,
  Pencil,
  ChevronLeft,
  ChevronRight,
  PanelLeftClose,
  PanelLeft,
} from "lucide-react";

interface LocalConversationItemProps {
  conversation: LocalConversation;
  isActive: boolean;
  onSelect: () => void;
  onDelete: () => void;
  onRename: (title: string) => void;
}

function LocalConversationItem({
  conversation,
  isActive,
  onSelect,
  onDelete,
  onRename,
}: LocalConversationItemProps) {
  const [showMenu, setShowMenu] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [editTitle, setEditTitle] = useState(conversation.title || "");

  const handleRename = () => {
    if (editTitle.trim()) {
      onRename(editTitle.trim());
    }
    setIsEditing(false);
  };

  const displayTitle =
    conversation.title || `Chat ${new Date(conversation.createdAt).toLocaleDateString()}`;

  return (
    <div
      className={cn(
        "group relative flex min-h-[44px] cursor-pointer items-center gap-2 rounded-lg px-3 py-3 text-sm transition-colors",
        isActive
          ? "bg-secondary text-secondary-foreground"
          : "text-muted-foreground hover:bg-secondary/50 hover:text-secondary-foreground"
      )}
      onClick={onSelect}
    >
      <MessageSquare className="h-4 w-4 shrink-0" />
      {isEditing ? (
        <input
          type="text"
          value={editTitle}
          onChange={(e) => setEditTitle(e.target.value)}
          onBlur={handleRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") handleRename();
            if (e.key === "Escape") setIsEditing(false);
          }}
          className="text-foreground flex-1 bg-transparent outline-none"
          autoFocus
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <span className="flex-1 truncate">{displayTitle}</span>
      )}

      <div className="relative">
        <Button
          variant="ghost"
          size="sm"
          className={cn(
            "touch:opacity-100 h-8 w-8 p-0 opacity-0 group-hover:opacity-100",
            showMenu && "opacity-100"
          )}
          onClick={(e) => {
            e.stopPropagation();
            setShowMenu(!showMenu);
          }}
        >
          <MoreVertical className="h-4 w-4" />
        </Button>

        {showMenu && (
          <>
            <div className="fixed inset-0 z-10" onClick={() => setShowMenu(false)} />
            <div className="bg-popover absolute top-8 right-0 z-20 w-40 rounded-md border shadow-lg">
              <button
                className="hover:bg-secondary flex min-h-[44px] w-full items-center gap-2 px-3 py-3 text-sm"
                onClick={(e) => {
                  e.stopPropagation();
                  setIsEditing(true);
                  setShowMenu(false);
                }}
              >
                <Pencil className="h-4 w-4" />
                Rename
              </button>
              <button
                className="text-destructive hover:bg-destructive/10 flex min-h-[44px] w-full items-center gap-2 px-3 py-3 text-sm"
                onClick={(e) => {
                  e.stopPropagation();
                  onDelete();
                  setShowMenu(false);
                }}
              >
                <Trash2 className="h-4 w-4" />
                Delete
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function ConversationList({ onNavigate }: { onNavigate?: () => void }) {
  const {
    conversations,
    currentConversationId,
    selectConversation,
    deleteConversation,
    renameConversation,
    createConversation,
  } = useLocalChatStore();

  const handleSelect = (id: string) => {
    selectConversation(id);
    onNavigate?.();
  };

  const handleNewChat = () => {
    createConversation();
    onNavigate?.();
  };

  return (
    <>
      <div className="p-3">
        <Button
          variant="outline"
          size="sm"
          className="h-10 w-full justify-start gap-2"
          onClick={handleNewChat}
        >
          <MessageSquarePlus className="h-4 w-4" />
          New Chat
        </Button>
      </div>

      <div className="scrollbar-thin flex-1 overflow-y-auto px-3 pb-3">
        {conversations.length === 0 ? (
          <div className="text-muted-foreground flex flex-col items-center justify-center py-8 text-center text-sm">
            <MessageSquare className="mb-2 h-8 w-8 opacity-50" />
            <p>No conversations yet</p>
            <p className="mt-1 text-xs">Start a new chat to begin</p>
          </div>
        ) : (
          <div className="space-y-1">
            {conversations.map((conversation) => (
              <LocalConversationItem
                key={conversation.id}
                conversation={conversation}
                isActive={conversation.id === currentConversationId}
                onSelect={() => handleSelect(conversation.id)}
                onDelete={() => deleteConversation(conversation.id)}
                onRename={(title) => renameConversation(conversation.id, title)}
              />
            ))}
          </div>
        )}
      </div>

      <div className="border-t px-3 py-2">
        <p className="text-muted-foreground text-center text-xs">Stored in browser</p>
      </div>
    </>
  );
}

interface LocalConversationSidebarProps {
  className?: string;
}

export function LocalConversationSidebar({ className }: LocalConversationSidebarProps) {
  const [isCollapsed, setIsCollapsed] = useState(false);
  const { isOpen, close } = useChatSidebarStore();

  if (isCollapsed) {
    return (
      <div
        className={cn(
          "bg-background hidden w-12 flex-col items-center border-r py-4 md:flex",
          className
        )}
      >
        <Button
          variant="ghost"
          size="sm"
          className="mb-4 h-10 w-10 p-0"
          onClick={() => setIsCollapsed(false)}
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-10 w-10 p-0"
          onClick={() => {
            useLocalChatStore.getState().createConversation();
          }}
          title="New Chat"
        >
          <MessageSquarePlus className="h-4 w-4" />
        </Button>
      </div>
    );
  }

  return (
    <>
      <aside
        className={cn("bg-background hidden w-64 shrink-0 flex-col border-r md:flex", className)}
      >
        <div className="flex h-12 items-center justify-between border-b px-4 py-3">
          <h2 className="text-sm font-semibold">Local Chats</h2>
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={() => setIsCollapsed(true)}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
        </div>
        <ConversationList />
      </aside>

      <Sheet open={isOpen} onOpenChange={close}>
        <SheetContent side="left" className="w-80 p-0">
          <SheetHeader className="h-12 px-4">
            <SheetTitle>Local Chats</SheetTitle>
            <SheetClose onClick={close} />
          </SheetHeader>
          <div className="flex h-[calc(100%-48px)] flex-col">
            <ConversationList onNavigate={close} />
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}

export function ChatSidebarToggle() {
  const { toggle, isOpen } = useChatSidebarStore();

  return (
    <Button variant="ghost" size="sm" className="h-10 w-10 p-0 md:hidden" onClick={toggle}>
      {isOpen ? <PanelLeftClose className="h-5 w-5" /> : <PanelLeft className="h-5 w-5" />}
      <span className="sr-only">Toggle chat list</span>
    </Button>
  );
}
