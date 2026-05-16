/**
 * API client tests — patch global fetch + assert the wrapper handles
 * status codes, auth headers, and JSON parsing the way we expect.
 */

import test, { beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";

import { loadConfig } from "./config.js";
import { ApiError, makeApiClient } from "./api.js";

let originalFetch: typeof globalThis.fetch;

beforeEach(() => {
  originalFetch = globalThis.fetch;
});
afterEach(() => {
  globalThis.fetch = originalFetch;
});

function mockFetch(
  responder: (url: string, init: RequestInit) => Response,
): void {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  globalThis.fetch = (async (input: any, init: any) => {
    const url = typeof input === "string" ? input : input.toString();
    return responder(url, init ?? {});
  }) as typeof globalThis.fetch;
}

test("api: legacy key (no hk_live_ prefix) sends Bearer + Cookie fallback", async () => {
  let capturedHeaders: Record<string, string> = {};
  mockFetch((_url, init) => {
    capturedHeaders = (init.headers as Record<string, string>) ?? {};
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  const api = makeApiClient(
    loadConfig({ HATCHIK_API_KEY: "session_abc" }),
  );
  await api.get("/api/account/me");
  assert.equal(capturedHeaders["Authorization"], "Bearer session_abc");
  // Cookie fallback fires because the key doesn't look like a proper
  // hk_live_ API key — covers the pre-Bearer cutover case where someone
  // pasted their session cookie value directly.
  assert.equal(capturedHeaders["Cookie"], "hatchik_session=session_abc");
});

test("api: proper hk_live_ key sends Bearer only, no Cookie", async () => {
  let capturedHeaders: Record<string, string> = {};
  mockFetch((_url, init) => {
    capturedHeaders = (init.headers as Record<string, string>) ?? {};
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  const api = makeApiClient(
    loadConfig({ HATCHIK_API_KEY: "hk_live_abcdef123456" }),
  );
  await api.get("/api/account/me");
  assert.equal(capturedHeaders["Authorization"], "Bearer hk_live_abcdef123456");
  assert.equal(capturedHeaders["Cookie"], undefined);
});

test("api: omits auth headers when no API key", async () => {
  let capturedHeaders: Record<string, string> = {};
  mockFetch((_url, init) => {
    capturedHeaders = (init.headers as Record<string, string>) ?? {};
    return new Response(JSON.stringify({}), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  });
  await makeApiClient(loadConfig({})).get("/api/account/me");
  assert.equal(capturedHeaders["Authorization"], undefined);
  assert.equal(capturedHeaders["Cookie"], undefined);
});

test("api: parses JSON 200 responses", async () => {
  mockFetch(() =>
    new Response(JSON.stringify({ email: "x@y.com" }), {
      status: 200,
      headers: { "content-type": "application/json" },
    }),
  );
  const r = await makeApiClient(loadConfig({})).get<{ email: string }>(
    "/api/account/me",
  );
  assert.equal(r.email, "x@y.com");
});

test("api: 204 returns null", async () => {
  mockFetch(() => new Response(null, { status: 204 }));
  const r = await makeApiClient(loadConfig({})).post("/api/account/logout");
  assert.equal(r, null);
});

test("api: 401 → friendly auth message via ApiError", async () => {
  mockFetch(() =>
    new Response(JSON.stringify({ detail: "not signed in" }), {
      status: 401,
      headers: { "content-type": "application/json" },
    }),
  );
  const api = makeApiClient(loadConfig({}));
  await assert.rejects(
    () => api.get("/api/account/me"),
    (err: unknown) => {
      assert.ok(err instanceof ApiError);
      assert.equal(err.status, 401);
      assert.match(err.message, /Not authorised/);
      assert.match(err.message, /HATCHIK_API_KEY/);
      return true;
    },
  );
});

test("api: 429 → rate-limit message", async () => {
  mockFetch(() =>
    new Response(JSON.stringify({ detail: "slow down" }), {
      status: 429,
      headers: { "content-type": "application/json" },
    }),
  );
  await assert.rejects(
    () => makeApiClient(loadConfig({})).post("/api/account/mobile-builds/x/trigger"),
    /Rate-limited.*slow down/,
  );
});

test("api: path must start with /", async () => {
  const api = makeApiClient(loadConfig({}));
  await assert.rejects(() => api.get("api/account/me"), /must start with/);
});

test("api: network error surfaces a hint about HATCHIK_API_URL", async () => {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  globalThis.fetch = (async () => {
    throw new TypeError("fetch failed");
  }) as typeof globalThis.fetch;
  await assert.rejects(
    () => makeApiClient(loadConfig({})).get("/api/account/me"),
    /Network error.*HATCHIK_API_URL/,
  );
});
