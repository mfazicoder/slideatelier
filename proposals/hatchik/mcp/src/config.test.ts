/**
 * Config tests — ensure env-var parsing puts us in the right mode and
 * fails loud on bad URLs.
 */

import test from "node:test";
import assert from "node:assert/strict";

import { loadConfig } from "./config.js";

test("loadConfig: no API key → signup mode", () => {
  const c = loadConfig({});
  assert.equal(c.mode, "signup");
  assert.equal(c.apiKey, null);
  assert.equal(c.apiUrl, "https://api.hatchik.com");
});

test("loadConfig: API key set → ops mode", () => {
  const c = loadConfig({ HATCHIK_API_KEY: "sk_test_123" });
  assert.equal(c.mode, "ops");
  assert.equal(c.apiKey, "sk_test_123");
});

test("loadConfig: trims trailing slashes from HATCHIK_API_URL", () => {
  const c = loadConfig({ HATCHIK_API_URL: "https://example.com///" });
  assert.equal(c.apiUrl, "https://example.com");
});

test("loadConfig: rejects malformed HATCHIK_API_URL", () => {
  assert.throws(
    () => loadConfig({ HATCHIK_API_URL: "not a url" }),
    /HATCHIK_API_URL is not a valid URL/,
  );
});

test("loadConfig: empty API key strings count as unset", () => {
  assert.equal(loadConfig({ HATCHIK_API_KEY: "" }).mode, "signup");
  assert.equal(loadConfig({ HATCHIK_API_KEY: "   " }).mode, "signup");
});

test("loadConfig: debug flag honours both '1' and 'true'", () => {
  assert.equal(loadConfig({ HATCHIK_MCP_DEBUG: "1" }).debug, true);
  assert.equal(loadConfig({ HATCHIK_MCP_DEBUG: "true" }).debug, true);
  assert.equal(loadConfig({ HATCHIK_MCP_DEBUG: "false" }).debug, false);
  assert.equal(loadConfig({}).debug, false);
});
