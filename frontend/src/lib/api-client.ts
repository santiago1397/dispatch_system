/**
 * Client-side API client.
 * All requests go through Next.js API routes (/api/*), never directly to the backend.
 * This keeps the backend URL hidden from the browser.
 */

export class ApiError extends Error {
  constructor(
    public status: number,
    public message: string,
    public data?: unknown
  ) {
    super(message);
    this.name = "ApiError";
  }
}

interface RequestOptions extends Omit<RequestInit, "body"> {
  /**
   * Query-string params. Accepts either a plain object (last write wins
   * per key — fine for most filters) or a pre-built ``URLSearchParams``
   * (use when the backend expects repeated keys, e.g. ``?kinds=a&kinds=b``).
   */
  params?: Record<string, string> | URLSearchParams;
  body?: unknown;
}

// Endpoints that must not trigger an auto-refresh on 401 — either they are
// the refresh call itself (would recurse) or a 401 is the expected outcome
// (login, where the caller decides what to do).
const REFRESH_SKIP_ENDPOINTS = new Set(["/auth/refresh", "/auth/login"]);

class ApiClient {
  private refreshInFlight: Promise<boolean> | null = null;

  private async request<T>(
    endpoint: string,
    options: RequestOptions = {},
    isRetry = false
  ): Promise<T> {
    const { params, body, ...fetchOptions } = options;

    let url = `/api${endpoint}`;

    if (params) {
      const qs =
        params instanceof URLSearchParams
          ? params.toString()
          : new URLSearchParams(params).toString();
      url += `?${qs}`;
    }

    const response = await fetch(url, {
      ...fetchOptions,
      headers: {
        "Content-Type": "application/json",
        ...fetchOptions.headers,
      },
      body: body ? JSON.stringify(body) : undefined,
    });

    if (
      response.status === 401 &&
      !isRetry &&
      !REFRESH_SKIP_ENDPOINTS.has(endpoint)
    ) {
      const refreshed = await this.refreshTokens();
      if (refreshed) {
        return this.request<T>(endpoint, options, true);
      }
    }

    if (!response.ok) {
      let errorData;
      try {
        errorData = await response.json();
      } catch {
        errorData = null;
      }
      throw new ApiError(
        response.status,
        errorData?.detail || errorData?.message || "Request failed",
        errorData
      );
    }

    // Handle empty responses
    const text = await response.text();
    if (!text) {
      return null as T;
    }

    return JSON.parse(text);
  }

  private async refreshTokens(): Promise<boolean> {
    if (this.refreshInFlight) return this.refreshInFlight;

    this.refreshInFlight = (async () => {
      try {
        const res = await fetch("/api/auth/refresh", { method: "POST" });
        return res.ok;
      } catch {
        return false;
      } finally {
        this.refreshInFlight = null;
      }
    })();

    return this.refreshInFlight;
  }

  get<T>(endpoint: string, options?: RequestOptions) {
    return this.request<T>(endpoint, { ...options, method: "GET" });
  }

  post<T>(endpoint: string, body?: unknown, options?: RequestOptions) {
    return this.request<T>(endpoint, { ...options, method: "POST", body });
  }

  put<T>(endpoint: string, body?: unknown, options?: RequestOptions) {
    return this.request<T>(endpoint, { ...options, method: "PUT", body });
  }

  patch<T>(endpoint: string, body?: unknown, options?: RequestOptions) {
    return this.request<T>(endpoint, { ...options, method: "PATCH", body });
  }

  delete<T>(endpoint: string, options?: RequestOptions) {
    return this.request<T>(endpoint, { ...options, method: "DELETE" });
  }
}

export const apiClient = new ApiClient();
