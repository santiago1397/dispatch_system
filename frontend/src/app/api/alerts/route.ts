import { NextRequest, NextResponse } from "next/server";
import { backendFetch, BackendApiError } from "@/lib/server-api";

/**
 * GET /api/alerts
 *
 * Lists pipeline alerts (default: open only). The backend accepts
 * ``resolved=true``, ``kinds=foo&kinds=bar`` (repeat the param), and
 * ``search=text`` (matches against the related job's raw message).
 */
export async function GET(request: NextRequest) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const sp = request.nextUrl.searchParams;
    const params = new URLSearchParams();
    if (sp.get("resolved")) params.set("resolved", sp.get("resolved")!);
    // Forward every ``kinds`` occurrence.
    for (const v of sp.getAll("kinds")) {
      params.append("kinds", v);
    }
    for (const key of ["search", "limit", "offset"]) {
      const v = sp.get(key);
      if (v) params.set(key, v);
    }

    const qs = params.toString();
    const path = `/api/v1/alerts${qs ? `?${qs}` : ""}`;

    const data = await backendFetch<unknown>(path, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Failed to fetch alerts" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}