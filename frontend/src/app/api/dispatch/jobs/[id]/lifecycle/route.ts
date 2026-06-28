import { NextRequest, NextResponse } from "next/server";
import { backendFetch, BackendApiError } from "@/lib/server-api";

/**
 * GET /api/dispatch/jobs/{id}/lifecycle
 *
 * Returns the append-only lifecycle events for a Job (timeline view).
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const { id } = await params;

    const sp = request.nextUrl.searchParams;
    const params2 = new URLSearchParams();
    for (const key of ["limit", "offset"]) {
      const v = sp.get(key);
      if (v) params2.set(key, v);
    }

    const qs = params2.toString();
    const path = `/api/v1/dispatch/jobs/${id}/lifecycle${qs ? `?${qs}` : ""}`;

    const data = await backendFetch<unknown>(path, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Failed to fetch lifecycle" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}

/**
 * PATCH /api/dispatch/jobs/{id}/lifecycle
 *
 * Manual lifecycle override from /jobs/[id]. Body:
 * ``{ to_status: LifecycleStatus, note?: string | null }``.
 *
 * The backend rejects ``to_status='closed'`` (closing flows through the
 * CLOSING_CHAT_JID group) and requires a non-empty ``note`` when
 * manually transitioning to ``canceled``.
 */
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> }
) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const { id } = await params;
    const body = await request.json();

    const data = await backendFetch<unknown>(
      `/api/v1/dispatch/jobs/${id}/lifecycle`,
      {
        method: "PATCH",
        headers: { Authorization: `Bearer ${accessToken}` },
        body: JSON.stringify(body),
      }
    );

    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Failed to set lifecycle status" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}