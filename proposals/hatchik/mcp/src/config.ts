/**
 * Hatchik MCP — configuration / env handling.
 *
 * Two modes:
 *   - signup-mode: no HATCHIK_API_KEY set. Only signup tools available.
 *   - ops-mode:    HATCHIK_API_KEY set. Full tool surface.
 *
 * API URL defaults to https://api.hatchik.com; override with HATCHIK_API_URL
 * for development against a local signup-service or staging deploy.
 */

export type Mode = "signup" | "ops";

export interface Config {
  mode: Mode;
  apiUrl: string;
  apiKey: string | null;
  /** Optional. Identifies the customer when signed in to a specific project. */
  projectId: string | null;
  /** Set true to log every HTTP call to stderr (never stdout — MCP stdio uses it). */
  debug: boolean;
}

const DEFAULT_API_URL = "https://api.hatchik.com";

export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const apiKey = env.HATCHIK_API_KEY?.trim() || null;
  const projectId = env.HATCHIK_PROJECT_ID?.trim() || null;
  // Strip trailing slash so callers can append paths without worrying.
  let apiUrl = (env.HATCHIK_API_URL?.trim() || DEFAULT_API_URL).replace(/\/+$/, "");
  const debug =
    env.HATCHIK_MCP_DEBUG === "1" || env.HATCHIK_MCP_DEBUG === "true";

  // Sanity-check URL — refuse to start with a malformed one rather than
  // producing baffling tool errors later.
  try {
    new URL(apiUrl);
  } catch {
    throw new Error(
      `HATCHIK_API_URL is not a valid URL: ${JSON.stringify(apiUrl)}`,
    );
  }

  return {
    mode: apiKey ? "ops" : "signup",
    apiUrl,
    apiKey,
    projectId,
    debug,
  };
}

/**
 * Log to stderr only. MCP servers communicate over stdio — anything written
 * to stdout corrupts the JSON-RPC stream and breaks the client.
 */
export function log(level: "info" | "warn" | "error" | "debug", ...args: unknown[]): void {
  // eslint-disable-next-line no-console
  console.error(`[hatchik-mcp ${level}]`, ...args);
}
