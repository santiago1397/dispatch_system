import { NextRequest, NextResponse } from "next/server";
import { backendFetch, BackendApiError } from "@/lib/server-api";

/**
 * GET /api/phone-bindings/suggestions — aggregate of observed regex
 * matches for unbound OpenPhone numbers. Forwards to
 * /api/v1/phone-bindings/suggestions.
 */
export async function GET(request: NextRequest) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }
    const data = await backendFetch<unknown>(
      "/api/v1/phone-bindings/suggestions",
      {
        headers: { Authorization: `Bearer ${accessToken}` },
      }
    );
    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Failed to fetch suggestions" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}
