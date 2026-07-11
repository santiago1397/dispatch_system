import { NextRequest, NextResponse } from "next/server";
import { backendFetch, BackendApiError } from "@/lib/server-api";
import type { OpenPhoneThreadList } from "@/types";

export async function GET(request: NextRequest) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const sp = request.nextUrl.searchParams;
    const params = new URLSearchParams();
    for (const key of ["phone_number_id", "skip", "limit"]) {
      const v = sp.get(key);
      if (v) params.set(key, v);
    }

    const qs = params.toString();
    const path = `/api/v1/openphone/threads${qs ? `?${qs}` : ""}`;

    const data = await backendFetch<OpenPhoneThreadList>(path, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });

    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Failed to fetch threads" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}
