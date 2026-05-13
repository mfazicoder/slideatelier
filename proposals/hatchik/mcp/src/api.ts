/**
 * Hatchik backend client.
 *
 * Thin wrapper over fetch() that adds:
 *   - Bearer auth header from config.apiKey
 *   - Common error mapping (so MCP tools can surface useful messages
 *     to the AI rather than raw HTTP status codes)
 *   - Request/response logging when config.debug is on
 *
 * The signup-service backend currently only supports session-cookie auth
 * (set by /api/account/login + /api/account/auth). API-key Bearer auth is
 * planned but not yet implemented — see TODO in main.py near _resolve_session.
 * For now, the MCP works in two ways:
 *
 *   1. Local dev: paste your hatchik_session cookie value into
 *      HATCHIK_API_KEY. The MCP sends it as `Cookie: hatchik_session=...`
 *      until the Bearer endpoint exists.
 *
 *   2. Once Bearer auth lands server-side, the MCP will send
 *      `Authorization: Bearer <key>` instead and the cookie fallback
 *      will be removed.
 *
 * Both modes go through this client so callers don't have to think about it.
 */

import { Config, log } from "./config.js";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly endpoint: string,
    public readonly body: unknown,
    message?: string,
  ) {
    super(
      message ??
        `Hatchik API ${endpoint} → ${status}: ${typeof body === "string" ? body : JSON.stringify(body)}`,
    );
    this.name = "ApiError";
  }
}

export interface ApiClient {
  get<T = unknown>(path: string): Promise<T>;
  post<T = unknown>(path: string, body?: unknown): Promise<T>;
  delete<T = unknown>(path: string): Promise<T>;
}

export function makeApiClient(config: Config): ApiClient {
  async function call<T>(
    method: "GET" | "POST" | "DELETE",
    path: string,
    body?: unknown,
  ): Promise<T> {
    if (!path.startsWith("/")) {
      throw new Error(`api path must start with "/": ${path}`);
    }
    const url = config.apiUrl + path;
    const headers: Record<string, string> = {
      "User-Agent": "@hatchik/mcp",
      Accept: "application/json",
    };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
    }
    if (config.apiKey) {
      // Bearer auth is now first-class server-side (POST /api/account/api-keys
      // issues `hk_live_<token>` strings). The server's _resolve_auth helper
      // prefers Bearer over Cookie when both are present.
      //
      // We still send the Cookie fallback for keys that look like raw
      // session IDs (pre-bearer cutover, customers who pasted their
      // hatchik_session cookie value). Once everyone's on hk_live_* tokens
      // the Cookie line can come out — but it's harmless to leave.
      headers["Authorization"] = `Bearer ${config.apiKey}`;
      if (!config.apiKey.startsWith("hk_live_")) {
        headers["Cookie"] = `hatchik_session=${config.apiKey}`;
      }
    }

    if (config.debug) {
      log("debug", method, url, body ? `body=${JSON.stringify(body)}` : "");
    }

    let response: Response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
      });
    } catch (err) {
      const cause = err instanceof Error ? err.message : String(err);
      throw new Error(
        `Network error calling Hatchik API at ${url}: ${cause}. ` +
          `Check HATCHIK_API_URL and your connection.`,
      );
    }

    // 204 No Content — return null cast as T.
    if (response.status === 204) {
      return null as T;
    }

    const contentType = response.headers.get("content-type") ?? "";
    const parsed: unknown = contentType.includes("application/json")
      ? await response.json().catch(() => null)
      : await response.text();

    if (!response.ok) {
      const friendly = friendlyMessage(response.status, path, parsed);
      throw new ApiError(response.status, path, parsed, friendly);
    }

    return parsed as T;
  }

  return {
    get: (path) => call("GET", path),
    post: (path, body) => call("POST", path, body),
    delete: (path) => call("DELETE", path),
  };
}

/**
 * Map HTTP errors to AI-friendly messages. The MCP surface is consumed by
 * an LLM that will paraphrase these to the human user — terse, actionable
 * sentences beat raw status codes.
 */
function friendlyMessage(
  status: number,
  path: string,
  body: unknown,
): string {
  const detail =
    typeof body === "object" && body !== null && "detail" in body
      ? String((body as { detail: unknown }).detail)
      : typeof body === "string"
        ? body
        : "";

  switch (status) {
    case 400:
      return `Bad request to ${path}${detail ? `: ${detail}` : ""}.`;
    case 401:
    case 403:
      return (
        `Not authorised on ${path}. ` +
        `Check HATCHIK_API_KEY is set in your MCP config — it should be ` +
        `your hatchik_session cookie value, copied from hatchik.com/account ` +
        `(while the Bearer-auth endpoint is being built out).`
      );
    case 404:
      return `${path} returned 404 — the resource doesn't exist or isn't visible to your account.`;
    case 429:
      return `Rate-limited on ${path}${detail ? ` — ${detail}` : ""}. Try again in a minute.`;
    case 500:
    case 502:
    case 503:
      return (
        `Hatchik API ${status} on ${path}. ` +
        `Either the service is restarting or something's genuinely broken — ` +
        `check https://status.hatchik.com.`
      );
    default:
      return `Hatchik API ${status} on ${path}${detail ? `: ${detail}` : ""}.`;
  }
}
