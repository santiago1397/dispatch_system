"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/hooks";
import { ROUTES } from "@/lib/constants";

export default function HomePage() {
  const { isAuthenticated, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (isLoading) return;
    router.replace(isAuthenticated ? ROUTES.DASHBOARD : ROUTES.LOGIN);
  }, [isAuthenticated, isLoading, router]);

  return null;
}
