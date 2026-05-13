/**
 * Tool tests — exercise each tool's handler against a stub ApiClient.
 *
 * Run from the package root:
 *   npm test
 *
 * Uses node:test (built-in, no extra runner needed) + tsx for TS support.
 */

import test from "node:test";
import assert from "node:assert/strict";

import type { ApiClient } from "../api.js";
import { projectInfoTool } from "./project-info.js";
import { listSandboxesTool } from "./list-sandboxes.js";
import { servicesTool } from "./services.js";
import { mobileBuildsListTool, mobileBuildTriggerTool } from "./mobile-builds.js";
import { startSignupStub } from "./signup-stubs.js";

function makeStubApi(responses: Record<string, unknown>): ApiClient {
  return {
    get: async (path: string) => {
      if (!(path in responses)) {
        throw new Error(`stub: no canned response for GET ${path}`);
      }
      return responses[path] as never;
    },
    post: async (path: string, _body?: unknown) => {
      if (!(path in responses)) {
        throw new Error(`stub: no canned response for POST ${path}`);
      }
      return responses[path] as never;
    },
    delete: async (path: string) => {
      if (!(path in responses)) {
        throw new Error(`stub: no canned response for DELETE ${path}`);
      }
      return responses[path] as never;
    },
  };
}

// ─── project_info ────────────────────────────────────────────────────

test("project_info: renders account + live tenants", async () => {
  const api = makeStubApi({
    "/api/account/me": {
      email: "alice@example.com",
      first_name: "Alice",
      github_username: "alicegit",
      sandboxes: [
        { slug: "prepsheet", product_name: "PrepSheet", status: "live", url: "https://prepsheet.hatchik.com", tier: "sandbox" },
        { slug: "old", product_name: "Old", status: "decommissioned", tier: "sandbox" },
      ],
    },
  });
  const t = projectInfoTool(api);
  const r = await t.handler({});
  assert.match(r.text, /alice@example\.com/);
  assert.match(r.text, /Alice/);
  assert.match(r.text, /@alicegit/);
  assert.match(r.text, /1 active tenant/);
  assert.match(r.text, /prepsheet/);
  // decommissioned tenant excluded
  assert.doesNotMatch(r.text, /old.*decommissioned/);
});

test("project_info: empty account handles gracefully", async () => {
  const api = makeStubApi({
    "/api/account/me": {
      email: "new@example.com",
      sandboxes: [],
    },
  });
  const r = await projectInfoTool(api).handler({});
  assert.match(r.text, /No active sandboxes yet/);
});

// ─── list_sandboxes ──────────────────────────────────────────────────

test("list_sandboxes: lists every tenant including decommissioned", async () => {
  const api = makeStubApi({
    "/api/account/me": {
      email: "x@y.com",
      sandboxes: [
        { slug: "a", product_name: "A", status: "live", tier: "launch", url: "https://a.com", repo_url: "https://gh/a" },
        { slug: "b", product_name: "B", status: "decommissioned", tier: "sandbox" },
      ],
    },
  });
  const r = await listSandboxesTool(api).handler({});
  assert.match(r.text, /2 tenants/);
  assert.match(r.text, /a — A/);
  assert.match(r.text, /b — B/);
  assert.match(r.text, /url: https:\/\/a\.com/);
});

test("list_sandboxes: empty returns helpful pointer", async () => {
  const api = makeStubApi({ "/api/account/me": { sandboxes: [] } });
  const r = await listSandboxesTool(api).handler({});
  assert.match(r.text, /No sandboxes yet/);
  assert.match(r.text, /hatchik\.com/);
});

// ─── services ────────────────────────────────────────────────────────

test("services: renders wired + available_on_upgrade sections", async () => {
  const api = makeStubApi({
    "/api/account/services/prepsheet": {
      slug: "prepsheet",
      sandbox_url: "https://prepsheet.hatchik.com",
      repo_url: "https://github.com/hatchik-sandboxes/prepsheet",
      tier: "sandbox",
      version: "2026.05.01",
      wired: [
        { name: "Postgres", detail: "Supabase", quota: "512 MB" },
        { name: "Mobile shells", detail: "iOS + Android" },
      ],
      available_on_upgrade: [
        { tier: "launch", name: "Custom domain", detail: "Bring your own or we register" },
      ],
    },
  });
  const r = await servicesTool(api).handler({ slug: "prepsheet" });
  assert.match(r.text, /tier sandbox/);
  assert.match(r.text, /inventory v2026\.05\.01/);
  assert.match(r.text, /✓ Postgres — Supabase \(512 MB\)/);
  assert.match(r.text, /✓ Mobile shells/);
  assert.match(r.text, /Available on upgrade:/);
  assert.match(r.text, /→ Custom domain \[launch\]/);
});

test("services: rejects empty slug", async () => {
  const api = makeStubApi({});
  await assert.rejects(
    () => servicesTool(api).handler({ slug: "" }),
    /String must contain at least 1 character|Required/,
  );
});

// ─── mobile_builds_list ──────────────────────────────────────────────

test("mobile_builds_list: renders icon + artefact URLs on success", async () => {
  const api = makeStubApi({
    "/api/account/mobile-builds/prepsheet": {
      builds: [
        {
          id: 7,
          status: "success",
          platforms: "both",
          created_at: "2026-05-13T12:00:00Z",
          ios_artifact_url: "https://gh/prepsheet/actions/runs/7/ios.ipa",
          android_artifact_url: "https://gh/prepsheet/actions/runs/7/android.apk",
        },
        {
          id: 6,
          status: "in_progress",
          platforms: "ios",
          created_at: "2026-05-13T12:30:00Z",
        },
      ],
    },
  });
  const r = await mobileBuildsListTool(api).handler({ slug: "prepsheet" });
  assert.match(r.text, /2 builds/);
  assert.match(r.text, /✓ #7 \[both\] success/);
  assert.match(r.text, /ios: https:\/\/gh\/prepsheet\/actions\/runs\/7\/ios\.ipa/);
  assert.match(r.text, /android: https:\/\/gh\/prepsheet\/actions\/runs\/7\/android\.apk/);
  assert.match(r.text, /… #6 \[ios\] in_progress/);
});

test("mobile_builds_list: no builds yet returns helpful pointer", async () => {
  const api = makeStubApi({
    "/api/account/mobile-builds/empty": { builds: [] },
  });
  const r = await mobileBuildsListTool(api).handler({ slug: "empty" });
  assert.match(r.text, /No mobile builds yet/);
  assert.match(r.text, /mobile_build_trigger/);
});

// ─── mobile_build_trigger ────────────────────────────────────────────

test("mobile_build_trigger: defaults to 'both' platforms", async () => {
  let capturedBody: unknown;
  const api: ApiClient = {
    get: async () => {
      throw new Error("not used");
    },
    post: async (path, body) => {
      assert.equal(path, "/api/account/mobile-builds/prepsheet/trigger");
      capturedBody = body;
      return { ok: true, message: "Build queued." };
    },
    delete: async () => {
      throw new Error("not used");
    },
  };
  const r = await mobileBuildTriggerTool(api).handler({ slug: "prepsheet" });
  assert.deepEqual(capturedBody, { platforms: "both" });
  assert.match(r.text, /✓ Build queued\./);
});

test("mobile_build_trigger: passes through platforms='ios'", async () => {
  let capturedBody: unknown;
  const api: ApiClient = {
    get: async () => {
      throw new Error("not used");
    },
    post: async (_path, body) => {
      capturedBody = body;
      return { ok: true, message: "iOS build queued." };
    },
    delete: async () => {
      throw new Error("not used");
    },
  };
  await mobileBuildTriggerTool(api).handler({ slug: "x", platforms: "ios" });
  assert.deepEqual(capturedBody, { platforms: "ios" });
});

test("mobile_build_trigger: rejects invalid platform", async () => {
  const api = makeStubApi({});
  await assert.rejects(
    () =>
      mobileBuildTriggerTool(api).handler({
        slug: "x",
        platforms: "windows",
      }),
    /Invalid enum value|invalid_enum_value/,
  );
});

// ─── signup stub ─────────────────────────────────────────────────────

test("signup stub: points at the web wizard until in-chat signup ships", async () => {
  const api = makeStubApi({});
  const r = await startSignupStub(api).handler({});
  assert.match(r.text, /hatchik\.com\/start/);
  assert.match(r.text, /HATCHIK_API_KEY/);
});
