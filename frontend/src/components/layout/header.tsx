"use client";

import Link from "next/link";
import { useAuth } from "@/hooks";
import { Button } from "@/components/ui";
import { ThemeToggle } from "@/components/theme";
import { ROUTES } from "@/lib/constants";
import { LogOut, User, Menu } from "lucide-react";
import { useSidebarStore } from "@/stores";

export function Header() {
  const { user, isAuthenticated, logout } = useAuth();
  const { toggle } = useSidebarStore();

  return (
    <header className="bg-background/95 supports-[backdrop-filter]:bg-background/60 sticky top-0 z-40 w-full border-b backdrop-blur">
      <div className="flex h-14 items-center justify-between px-3 sm:px-6">
        <Button variant="ghost" size="sm" className="h-10 w-10 p-0 md:hidden" onClick={toggle}>
          <Menu className="h-5 w-5" />
          <span className="sr-only">Toggle menu</span>
        </Button>

        <div className="hidden md:block" />

        <div className="flex items-center gap-2 sm:gap-3">
          <ThemeToggle />
          {isAuthenticated ? (
            <>
              <Button variant="ghost" size="sm" asChild className="h-10 px-2 sm:px-3">
                <Link href={ROUTES.PROFILE} className="flex items-center gap-2">
                  <User className="h-4 w-4" />
                  <span className="hidden max-w-32 truncate sm:inline">{user?.email}</span>
                </Link>
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={logout}
                className="h-10 w-10 p-0 sm:w-auto sm:px-3"
              >
                <LogOut className="h-4 w-4" />
                <span className="sr-only sm:not-sr-only sm:ml-2">Logout</span>
              </Button>
            </>
          ) : (
            <>
              <Button variant="ghost" size="sm" asChild className="h-10">
                <Link href={ROUTES.LOGIN}>Login</Link>
              </Button>
              <Button size="sm" asChild className="h-10">
                <Link href={ROUTES.REGISTER}>Register</Link>
              </Button>
            </>
          )}
        </div>
      </div>
    </header>
  );
}
