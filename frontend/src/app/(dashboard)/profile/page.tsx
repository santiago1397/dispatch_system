"use client";

import { useState } from "react";
import { useAuth } from "@/hooks";
import { Button, Card, Input, Label, Badge } from "@/components/ui";
import { ThemeToggle } from "@/components/theme";
import { User, Mail, Calendar, Shield, Settings } from "lucide-react";

export default function ProfilePage() {
  const { user, isAuthenticated, logout } = useAuth();
  const [isEditing, setIsEditing] = useState(false);

  if (!isAuthenticated || !user) {
    return (
      <div className="flex min-h-[50vh] items-center justify-center">
        <Card className="mx-4 p-6 text-center sm:p-8">
          <p className="text-muted-foreground">Please log in to view your profile.</p>
        </Card>
      </div>
    );
  }

  return (
    <div className="container mx-auto max-w-4xl">
      <div className="mb-6 sm:mb-8">
        <h1 className="text-2xl font-bold tracking-tight sm:text-3xl">Profile</h1>
        <p className="text-muted-foreground text-sm sm:text-base">
          Manage your account settings and preferences
        </p>
      </div>

      <div className="grid gap-4 sm:gap-6">
        <Card className="p-4 sm:p-6">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
            <div className="flex items-center gap-3 sm:gap-4">
              <div className="bg-primary/10 flex h-12 w-12 shrink-0 items-center justify-center rounded-full sm:h-16 sm:w-16">
                <User className="text-primary h-6 w-6 sm:h-8 sm:w-8" />
              </div>
              <div className="min-w-0">
                <h2 className="truncate text-lg font-semibold sm:text-xl">{user.email}</h2>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  {user.is_superuser && (
                    <Badge variant="secondary">
                      <Shield className="mr-1 h-3 w-3" />
                      Admin
                    </Badge>
                  )}
                  {user.is_active && (
                    <Badge variant="outline" className="text-green-600">
                      Active
                    </Badge>
                  )}
                </div>
              </div>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => setIsEditing(!isEditing)}
              className="h-10 self-start"
            >
              <Settings className="mr-2 h-4 w-4" />
              {isEditing ? "Cancel" : "Edit"}
            </Button>
          </div>
        </Card>

        <Card className="p-4 sm:p-6">
          <h3 className="mb-4 text-base font-semibold sm:text-lg">Account Information</h3>
          <div className="grid gap-4">
            <div className="grid gap-2">
              <Label htmlFor="email" className="flex items-center gap-2 text-sm">
                <Mail className="text-muted-foreground h-4 w-4" />
                Email Address
              </Label>
              <Input
                id="email"
                type="email"
                value={user.email}
                disabled={!isEditing}
                className={!isEditing ? "bg-muted" : ""}
              />
            </div>

            {user.created_at && (
              <div className="text-muted-foreground flex items-center gap-2 text-xs sm:text-sm">
                <Calendar className="h-4 w-4 shrink-0" />
                <span>Member since {new Date(user.created_at).toLocaleDateString()}</span>
              </div>
            )}
          </div>

          {isEditing && (
            <div className="mt-4 flex flex-col justify-end gap-2 sm:flex-row">
              <Button variant="outline" onClick={() => setIsEditing(false)} className="h-10">
                Cancel
              </Button>
              <Button className="h-10">Save Changes</Button>
            </div>
          )}
        </Card>

        <Card className="p-4 sm:p-6">
          <h3 className="mb-4 text-base font-semibold sm:text-lg">Preferences</h3>
          <div className="flex items-center justify-between gap-4">
            <div className="min-w-0">
              <p className="text-sm font-medium sm:text-base">Theme</p>
              <p className="text-muted-foreground text-xs sm:text-sm">
                Choose your preferred color scheme
              </p>
            </div>
            <ThemeToggle variant="dropdown" />
          </div>
        </Card>

        <Card className="border-destructive/50 p-4 sm:p-6">
          <h3 className="text-destructive mb-4 text-base font-semibold sm:text-lg">Danger Zone</h3>
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-sm font-medium sm:text-base">Sign out</p>
              <p className="text-muted-foreground text-xs sm:text-sm">
                Sign out from your account on this device
              </p>
            </div>
            <Button variant="destructive" onClick={logout} className="h-10 self-start sm:self-auto">
              Sign Out
            </Button>
          </div>
        </Card>
      </div>
    </div>
  );
}
