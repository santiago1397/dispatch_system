import { NextRequest, NextResponse } from "next/server";

/**
 * GET /api/stats/export
 *
 * Streams a CSV or JSON download of the snapshot rows. We pass the
 * Authorization header but **do not** parse the JSON — the response is
 * a streamed file and we proxy it through unchanged.
 */
export async function GET(request: NextRequest) {
  const accessToken = request.cookies.get("access_token")?.value;
  if (!accessToken) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }

  const sp = request.nextUrl.searchParams;
  const params = new URLSearchParams();
  for (const key of ["snapshot_date", "date", "scope", "format"]) {
    const v = sp.get(key);
    if (v) params.set(key, v);
  }

  const qs = params.toString();
  const path = `/api/v1/stats/export${qs ? `?${qs}` : ""}`;

  const BACKEND_URL = process.env.BACKEND_URL || "http://localhost:8888";
  let response: Response;
  try {
    response = await fetch(`${BACKEND_URL}${path}`, {
      headers: { Authorization: `Bearer ${accessToken}` },
    });
  } catch (err) {
    return NextResponse.json(
      { detail: `Backend unreachable: ${err instanceof Error ? err.message : "unknown"}` },
      { status: 502 }
    );
  }

  if (!response.ok) {
    const errorText = await response.text();
    return NextResponse.json(
      { detail: errorText || "Failed to export stats" },
      { status: response.status }
    );
  }

  // Forward the file body + content-disposition header so the browser
  // downloads it with the right filename.
  const headers = new Headers();
  const contentType = response.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const disposition = response.headers.get("content-disposition");
  if (disposition) headers.set("content-disposition", disposition);

  return new NextResponse(response.body, { headers });
}