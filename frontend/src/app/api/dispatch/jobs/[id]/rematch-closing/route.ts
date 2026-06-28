import { NextRequest, NextResponse } from "next/server";
import { backendFetch, BackendApiError } from "@/lib/server-api";

/**
 * POST /api/dispatch/jobs/{id}/rematch-closing
 *
 * Replays the closing-to-Job matching for a ``closing_unmatched`` row.
 * Used when the original Job lands after the closing message was already
 * processed. Backend returns the updated DispatchJob — it transitions
 * to ``closed`` on success or stays ``closing_unmatched`` if no match.
 */
export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const { id } = await params;
    const data = await backendFetch<unknown>(
      `/api/v1/dispatch/jobs/${id}/rematch-closing`,
      {
        method: "POST",
        headers: { Authorization: `Bearer ${accessToken}` },
      }
    );

    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Rematch failed" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}
