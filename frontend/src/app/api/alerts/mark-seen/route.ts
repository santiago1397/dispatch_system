import { NextRequest, NextResponse } from "next/server";
import { backendFetch, BackendApiError } from "@/lib/server-api";

/**
 * POST /api/alerts/mark-seen
 *
 * Marks every open alert as seen, clearing the navbar's unseen count.
 * Does not resolve anything.
 */
export async function POST(request: NextRequest) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const data = await backendFetch<unknown>("/api/v1/alerts/mark-seen", {
      method: "POST",
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Failed to mark alerts seen" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}
