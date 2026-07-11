"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { ROUTES } from "@/lib/constants";
import {
  AlertTriangle,
  BarChart3,
  ClipboardList,
  FileText,
  LayoutDashboard,
  MessagesSquare,
  Settings,
  Users,
} from "lucide-react";
import { useSidebarStore } from "@/stores";
import { Sheet, SheetContent, SheetHeader, SheetTitle, SheetClose } from "@/components/ui";
import { useAuth } from "@/hooks";
import { AlertsSidebarBadge } from "@/components/alerts";

const baseNavigation = [
  { name: "Dashboard", href: ROUTES.DASHBOARD, icon: LayoutDashboard },
  { name: "Jobs", href: ROUTES.JOBS, icon: ClipboardList },
  { name: "Alerts", href: ROUTES.ALERTS, icon: AlertTriangle, badge: AlertsSidebarBadge },
  { name: "Stats", href: ROUTES.STATS, icon: BarChart3 },
  { name: "Reports", href: ROUTES.REPORTS, icon: FileText },
  { name: "WhatsApp", href: ROUTES.WHATSAPP, icon: MessagesSquare },
];

const adminNavigation = [
  { name: "Technicians", href: ROUTES.TECHNICIANS, icon: Users },
  { name: "Chat roles", href: ROUTES.CHAT_ROLES, icon: MessagesSquare },
  { name: "Settings", href: ROUTES.SETTINGS, icon: Settings },
];

function NavLink({
  href,
  icon: Icon,
  name,
  badge: Badge,
  onNavigate,
  isActive,
}: {
  href: string;
  icon: typeof LayoutDashboard;
  name: string;
  badge?: () => React.ReactNode;
  onNavigate?: () => void;
  isActive: boolean;
}) {
  return (
    <Link
      href={href}
      onClick={onNavigate}
      className={cn(
        "flex items-center gap-3 rounded-lg px-3 py-3 text-sm font-medium transition-colors",
        "min-h-[44px]",
        isActive
          ? "bg-secondary text-secondary-foreground"
          : "text-muted-foreground hover:bg-secondary/50 hover:text-secondary-foreground"
      )}
    >
      <Icon className="h-5 w-5" />
      <span className="flex-1">{name}</span>
      {Badge ? <Badge /> : null}
    </Link>
  );
}

function SectionHeading({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-muted-foreground mt-4 mb-1 px-3 text-[10px] font-semibold tracking-wider uppercase first:mt-0">
      {children}
    </h3>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { user } = useAuth();
  const isAdmin = user?.role === "admin" || user?.is_superuser === true;

  return (
    <nav className="flex-1 space-y-1 overflow-y-auto p-4">
      <SectionHeading>Dispatch ops</SectionHeading>
      {baseNavigation.map((item) => {
        const isActive =
          pathname === item.href || pathname.startsWith(`${item.href}/`);
        return (
          <NavLink
            key={item.name}
            href={item.href}
            icon={item.icon}
            name={item.name}
            badge={item.badge}
            isActive={isActive}
            onNavigate={onNavigate}
          />
        );
      })}

      {isAdmin ? (
        <>
          <SectionHeading>Admin</SectionHeading>
          {adminNavigation.map((item) => {
            const isActive =
              pathname === item.href || pathname.startsWith(`${item.href}/`);
            return (
              <NavLink
                key={item.name}
                href={item.href}
                icon={item.icon}
                name={item.name}
                isActive={isActive}
                onNavigate={onNavigate}
              />
            );
          })}
        </>
      ) : null}
    </nav>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <div className="flex h-full flex-col">
      <div className="flex h-14 items-center border-b px-4">
        <Link
          href={ROUTES.HOME}
          className="flex items-center gap-2 font-semibold"
          onClick={onNavigate}
        >
          <span>{"agents_bots"}</span>
        </Link>
      </div>
      <NavLinks onNavigate={onNavigate} />
    </div>
  );
}

export function Sidebar() {
  const { isOpen, close } = useSidebarStore();

  return (
    <>
      <aside className="bg-background hidden w-64 shrink-0 border-r md:block">
        <SidebarContent />
      </aside>

      <Sheet open={isOpen} onOpenChange={close}>
        <SheetContent side="left" className="w-72 p-0">
          <SheetHeader className="h-14 px-4">
            <SheetTitle>{"agents_bots"}</SheetTitle>
            <SheetClose onClick={close} />
          </SheetHeader>
          <NavLinks onNavigate={close} />
        </SheetContent>
      </Sheet>
    </>
  );
}