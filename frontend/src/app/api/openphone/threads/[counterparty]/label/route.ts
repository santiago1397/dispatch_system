import { NextRequest, NextResponse } from "next/server";
import { backendFetch, BackendApiError } from "@/lib/server-api";
import type { OpenPhoneThreadLabelRead } from "@/types";

interface RouteContext {
  params: Promise<{ counterparty: string }>;
}

/**
 * PUT /api/openphone/threads/{counterparty}/label
 *
 * Set a thread's company reference and/or free-text label. Display-only —
 * never touches classification. Body: `{ company_id?: string | null, label?: string | null }`.
 */
export async function PUT(request: NextRequest, context: RouteContext) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const { counterparty } = await context.params;
    const body = await request.json();

    const data = await backendFetch<OpenPhoneThreadLabelRead>(
      `/api/v1/openphone/threads/${encodeURIComponent(counterparty)}/label`,
      {
        method: "PUT",
        headers: { Authorization: `Bearer ${accessToken}` },
        body: JSON.stringify(body),
      }
    );

    return NextResponse.json(data);
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Failed to set thread label" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}

/**
 * DELETE /api/openphone/threads/{counterparty}/label
 *
 * Clear a thread's company reference/label. No-op if unset.
 */
export async function DELETE(request: NextRequest, context: RouteContext) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const { counterparty } = await context.params;

    await backendFetch<unknown>(
      `/api/v1/openphone/threads/${encodeURIComponent(counterparty)}/label`,
      {
        method: "DELETE",
        headers: { Authorization: `Bearer ${accessToken}` },
      }
    );

    return new NextResponse(null, { status: 204 });
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Failed to clear thread label" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}
