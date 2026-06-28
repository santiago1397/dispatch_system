import { NextRequest, NextResponse } from "next/server";
import { backendFetch, BackendApiError } from "@/lib/server-api";

/**
 * Proxy for /api/v1/settings/llm — GET (view), PUT (update), DELETE (reset).
 * All three require an admin JWT (enforced on the backend).
 */

const BACKEND_PATH = "/api/v1/settings/llm";

function authHeader(request: NextRequest): { Authorization: string } | null {
  const token = request.cookies.get("access_token")?.value;
  return token ? { Authorization: `Bearer ${token}` } : null;
}

function errorResponse(error: unknown, fallback: string) {
  if (error instanceof BackendApiError) {
    return NextResponse.json(
      { detail: error.message || fallback },
      { status: error.status }
    );
  }
  return NextResponse.json({ detail: "Internal server error" }, { status: 500 });
}

export async function GET(request: NextRequest) {
  const headers = authHeader(request);
  if (!headers) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }
  try {
    const data = await backendFetch<unknown>(BACKEND_PATH, { headers });
    return NextResponse.json(data);
  } catch (error) {
    return errorResponse(error, "Failed to fetch LLM settings");
  }
}

export async function PUT(request: NextRequest) {
  const headers = authHeader(request);
  if (!headers) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }
  try {
    const body = await request.text();
    const data = await backendFetch<unknown>(BACKEND_PATH, {
      method: "PUT",
      headers,
      body,
    });
    return NextResponse.json(data);
  } catch (error) {
    return errorResponse(error, "Failed to update LLM settings");
  }
}

export async function DELETE(request: NextRequest) {
  const headers = authHeader(request);
  if (!headers) {
    return NextResponse.json({ detail: "Not authenticated" }, { status: 401 });
  }
  try {
    const data = await backendFetch<unknown>(BACKEND_PATH, {
      method: "DELETE",
      headers,
    });
    return NextResponse.json(data);
  } catch (error) {
    return errorResponse(error, "Failed to reset LLM settings");
  }
}
