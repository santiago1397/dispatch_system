import Link from "next/link";
import { Button, Card, CardHeader, CardTitle, CardContent } from "@/components/ui";
import { ROUTES } from "@/lib/constants";

export default function HomePage() {
  return (
    <div className="bg-background min-h-screen">
      <div className="container mx-auto px-4 py-16">
        <div className="mb-12 text-center">
          <h1 className="mb-4 text-4xl font-bold">agents_bots</h1>
          <p className="text-muted-foreground mx-auto max-w-2xl text-xl">
            Project to work as microservice axuliar for main application
          </p>
        </div>

        <div className="mx-auto grid max-w-5xl gap-6 md:grid-cols-2 lg:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle>Authentication</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground mb-4">Secure JWT-based authentication system</p>
              <div className="flex gap-2">
                <Button asChild>
                  <Link href={ROUTES.LOGIN}>Login</Link>
                </Button>
                <Button variant="outline" asChild>
                  <Link href={ROUTES.REGISTER}>Register</Link>
                </Button>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>AI Assistant</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground mb-4">
                Chat with our AI assistant powered by PydanticAI
              </p>
              <Button asChild>
                <Link href={ROUTES.CHAT}>Start Chat</Link>
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Dashboard</CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-muted-foreground mb-4">
                View your dashboard and manage your account
              </p>
              <Button variant="outline" asChild>
                <Link href={ROUTES.DASHBOARD}>Go to Dashboard</Link>
              </Button>
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
