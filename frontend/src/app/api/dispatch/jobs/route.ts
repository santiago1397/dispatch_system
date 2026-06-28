import { NextRequest, NextResponse } from "next/server";
import { backendFetch, BackendApiError } from "@/lib/server-api";

/**
 * GET /api/dispatch/jobs
 *
 * Lists dispatch jobs with optional filters (status, company_id,
 * since, until) and pagination (skip, limit). The Next.js route
 * forwards to the FastAPI backend at /api/v1/dispatch/jobs.
 */
export async function GET(request: NextRequest) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const sp = request.nextUrl.searchParams;
    const params = new URLSearchParams();
    for (const key of [
      "status",
      "company_id",
      "since",
      "until",
      "skip",
      "limit",
      "q",
    ]) {
      const v = sp.get(key);
      if (v) params.set(key, v);
    }

    const qs = params.toString();
    const path = `/api/v1/dispatch/jobs${qs ? `?${qs}` : ""}`;

    const data = await backendFetch<unknown>(path, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Failed to fetch jobs" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}
