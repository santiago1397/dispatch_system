import { NextRequest, NextResponse } from "next/server";
import { backendFetch, BackendApiError } from "@/lib/server-api";

/**
 * PATCH /api/whatsapp/tracked-chats/{jid}
 *
 * Update a tracked chat's display name, is_active flag, or chat_role.
 * Body: ``{ display_name?: string, is_active?: boolean, chat_role?: string }``.
 *
 * The ``chat_role`` field is what the /chat-roles admin page flips to
 * tag a chat as a tech dispatch chat (``tech_dispatch``) — the
 * downstream ingest_batch router branches on this.
 */
export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ jid: string }> }
) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const { jid } = await params;
    const body = await request.json();

    // chat_jid may contain characters that need URL encoding (the `@g.us`
    // piece is fine, but be defensive).
    const encodedJid = encodeURIComponent(jid);
    const data = await backendFetch<unknown>(
      `/api/v1/whatsapp/tracked-chats/${encodedJid}`,
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
        { detail: error.message || "Failed to update tracked chat" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}

/**
 * DELETE /api/whatsapp/tracked-chats/{jid}
 *
 * Soft-delete a tracked chat (sets is_active=False).
 */
export async function DELETE(
  request: NextRequest,
  { params }: { params: Promise<{ jid: string }> }
) {
  try {
    const accessToken = request.cookies.get("access_token")?.value;
    if (!accessToken) {
      return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
    }

    const { jid } = await params;
    const encodedJid = encodeURIComponent(jid);
    await backendFetch<unknown>(
      `/api/v1/whatsapp/tracked-chats/${encodedJid}`,
      {
        method: "DELETE",
        headers: { Authorization: `Bearer ${accessToken}` },
      }
    );

    return new NextResponse(null, { status: 204 });
  } catch (error) {
    if (error instanceof BackendApiError) {
      return NextResponse.json(
        { detail: error.message || "Failed to deactivate tracked chat" },
        { status: error.status }
      );
    }
    return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
  }
}