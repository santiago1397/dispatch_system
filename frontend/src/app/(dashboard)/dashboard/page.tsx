"use client";

import { useEffect, useState } from "react";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui";
import { apiClient } from "@/lib/api-client";
import { useAuth } from "@/hooks";
import type { HealthResponse } from "@/types";
import { CheckCircle, XCircle, Loader2 } from "lucide-react";

export default function DashboardPage() {
  const { user } = useAuth();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [healthLoading, setHealthLoading] = useState(true);
  const [healthError, setHealthError] = useState(false);

  useEffect(() => {
    const checkHealth = async () => {
      try {
        const data = await apiClient.get<HealthResponse>("/health");
        setHealth(data);
        setHealthError(false);
      } catch {
        setHealthError(true);
      } finally {
        setHealthLoading(false);
      }
    };

    checkHealth();
  }, []);

  return (
    <div className="space-y-4 sm:space-y-6">
      <div>
        <h1 className="text-2xl font-bold sm:text-3xl">Dashboard</h1>
        <p className="text-muted-foreground text-sm sm:text-base">
          Welcome back{user?.name ? `, ${user.name}` : ""}!
        </p>
      </div>

      <div className="grid gap-4 sm:gap-6 md:grid-cols-2 lg:grid-cols-3">
        <Card>
          <CardHeader className="pb-2 sm:pb-4">
            <CardTitle className="flex items-center gap-2 text-base sm:text-lg">
              API Status
              {healthLoading ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : healthError ? (
                <XCircle className="text-destructive h-4 w-4" />
              ) : (
                <CheckCircle className="h-4 w-4 text-green-500" />
              )}
            </CardTitle>
          </CardHeader>
          <CardContent>
            {healthLoading ? (
              <p className="text-muted-foreground text-sm">Checking...</p>
            ) : healthError ? (
              <p className="text-destructive text-sm">Backend unavailable</p>
            ) : (
              <div className="space-y-1">
                <p className="text-sm">
                  Status: <span className="font-medium">{health?.status}</span>
                </p>
                {health?.version && (
                  <p className="text-muted-foreground text-xs sm:text-sm">
                    Version: {health.version}
                  </p>
                )}
              </div>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2 sm:pb-4">
            <CardTitle className="text-base sm:text-lg">Your Account</CardTitle>
          </CardHeader>
          <CardContent>
            {user ? (
              <div className="space-y-1">
                <p className="text-sm break-all">
                  Email: <span className="font-medium">{user.email}</span>
                </p>
                {user.name && (
                  <p className="text-sm">
                    Name: <span className="font-medium">{user.name}</span>
                  </p>
                )}
              </div>
            ) : (
              <p className="text-muted-foreground text-sm">Loading...</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
