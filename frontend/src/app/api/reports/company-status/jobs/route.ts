import { NextRequest, NextResponse } from "next/server";
import { backendFetch, BackendApiError } from "@/lib/server-api";

/**
 * GET /api/reports/company-status/jobs
 *
 * Proxies the job-level drill-down behind one company/bucket cell of the
 * company-status report. No caching — recomputed against the backend
 * every call, same as the parent report.
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
      "company_id",
      "bucket",
      "start_date",
      "end_date",
      "include_scheduled_appts",
    ]) {
      const v = sp.get(key);
      if (v) params.set(key, v);
    }

    const qs = params.toString();
    const path = `/api/v1/reports/company-status/jobs${qs ? `?${qs}` : ""}`;

    const data = await backendFetch<unknown>(path, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Failed to fetch report jobs" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}
