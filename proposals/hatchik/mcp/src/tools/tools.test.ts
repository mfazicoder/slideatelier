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
import {
  startSignupTool, suggestDomainsTool, checkDomainTool, setChoicesTool,
  quoteTool, checkoutTool, statusTool, completeTool,
} from "./signup.js";
import {
  deployStatusTool, previewUrlTool, pendingMigrationsTool,
  readLogsTool, recentErrorsTool,
  deployToProdTool, applyMigrationTool, rollbackTool, teamInviteTool,
  cancelSubscriptionTool,
} from "./ops-advanced.js";

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
    patch: async (path: string, _body?: unknown) => {
      if (!(path in responses)) {
        throw new Error(`stub: no canned response for PATCH ${path}`);
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

// ─── signup tools ────────────────────────────────────────────────────

test("start_signup: returns a session_id and forwards the description", async () => {
  const api = makeStubApi({
    "/api/wizard/sessions": {
      session_id: "ws_abcdef1234567890ABCDEF12",
      status: "new",
      expires_at: new Date(Date.now() + 86400000).toISOString(),
      choices: { description: "meal-prep app" },
    },
  });
  const r = await startSignupTool(api).handler({ description: "meal-prep app" });
  assert.match(r.text, /ws_abcdef/);
  assert.match(r.text, /suggest_domains/);
});

test("suggest_domains: lists candidates and flags premium TLDs", async () => {
  const api = makeStubApi({
    "/api/wizard/sessions/ws_x/suggest-domains": {
      session_id: "ws_x", base_name: "mealmate",
      suggestions: [
        { domain: "mealmate.com", available: false, price_pence: 1400,
          premium: false, coverage_pence: 1400, customer_pence: 0 },
        { domain: "mealmate.app", available: true, price_pence: 1400,
          premium: false, coverage_pence: 1400, customer_pence: 0 },
        { domain: "mealmate.io", available: true, price_pence: 3000,
          premium: true, coverage_pence: 1400, customer_pence: 1600 },
      ],
    },
  });
  const r = await suggestDomainsTool(api).handler({
    session_id: "ws_x", base_name: "mealmate",
  });
  assert.match(r.text, /mealmate\.com/);
  assert.match(r.text, /mealmate\.io.*premium/);
});

test("check_domain: reports availability + customer share", async () => {
  const api = makeStubApi({
    "/api/wizard/sessions/ws_x/check-domain?domain=foo.ai": {
      domain: "foo.ai", available: true, price_pence: 9000,
      premium: true, coverage_pence: 1400, customer_pence: 7600,
    },
  });
  const r = await checkDomainTool(api).handler({
    session_id: "ws_x", domain: "foo.ai",
  });
  assert.match(r.text, /foo\.ai/);
  assert.match(r.text, /£76\.00/);
});

test("set_choices: confirms what got recorded", async () => {
  const api = makeStubApi({
    "/api/wizard/sessions/ws_x": {
      id: "ws_x", status: "in_progress",
      choices: { tier: "launch", domain: "mealmate.app", email: "alex@x" },
    },
  });
  const r = await setChoicesTool(api).handler({
    session_id: "ws_x",
    choices: { tier: "launch", domain: "mealmate.app", email: "alex@x" },
  });
  assert.match(r.text, /tier: launch/);
  assert.match(r.text, /mealmate\.app/);
});

test("quote: renders setup + monthly + year-1 totals", async () => {
  const api = makeStubApi({
    "/api/wizard/sessions/ws_x/quote": {
      session_id: "ws_x",
      quote: {
        tier: "launch",
        setup_pence: 8900, setup_display: "£89.00",
        monthly_pence: 1400, monthly_display: "£14.00",
        monthly_billing_cycle: "annual",
        domain_passthrough_pence: 0, domain_passthrough_display: "£0.00",
        year_one_display: "£257.00",
        breakdown: [
          { label: "Launch setup", pence: 8900, kind: "one-off" },
          { label: "Launch monthly (annual billing)", pence: 1400, kind: "recurring-monthly" },
        ],
      },
      choices: {},
    },
  });
  const r = await quoteTool(api).handler({ session_id: "ws_x" });
  assert.match(r.text, /Launch setup/);
  assert.match(r.text, /Year-1 total: £257\.00/);
});

test("checkout (sandbox): returns install_token + skips Paddle", async () => {
  const api = makeStubApi({
    "/api/wizard/sessions/ws_x/checkout": {
      session_id: "ws_x", tier: "sandbox",
      checkout_required: false,
      checkout_url: null,
      install_token: "it_abcdefghij1234567890ABCDEFGHIJ12",
      status: "provisioning",
      message: "Sandbox tier is free — provisioning started immediately.",
    },
  });
  const r = await checkoutTool(api).handler({ session_id: "ws_x" });
  assert.match(r.text, /install_token: it_abcdef/);
  assert.match(r.text, /status/);
});

test("checkout (launch): returns a Paddle URL", async () => {
  const api = makeStubApi({
    "/api/wizard/sessions/ws_y/checkout": {
      session_id: "ws_y", tier: "launch",
      checkout_required: true,
      checkout_url: "https://buy.paddle.com/checkout/pri_xyz?...",
      status: "awaiting_pay",
    },
  });
  const r = await checkoutTool(api).handler({ session_id: "ws_y" });
  assert.match(r.text, /buy\.paddle\.com/);
  assert.match(r.text, /status/);
});

test("status (ready): tells the AI to call complete", async () => {
  const api = makeStubApi({
    "/api/wizard/sessions/ws_x/status": {
      session_id: "ws_x", status: "ready",
      signup_status: "live-sandbox",
      product_name: "MealMate",
      domain: "mealmate.app",
      install_token_available: true,
    },
  });
  const r = await statusTool(api).handler({ session_id: "ws_x" });
  assert.match(r.text, /ready/);
  assert.match(r.text, /complete/);
});

test("complete: returns api_key + MCP config block", async () => {
  const api = makeStubApi({
    "/api/wizard/sessions/ws_x/complete": {
      ok: true, session_id: "ws_x", signup_id: 42,
      api_key: "hk_live_abcdefghij",
      api_url: "https://api.hatchik.com",
      project: {
        id: "42", product_name: "MealMate", domain: "mealmate.app", tier: "launch",
      },
    },
  });
  const r = await completeTool(api).handler({
    session_id: "ws_x", install_token: "it_xyz",
  });
  assert.match(r.text, /HATCHIK_API_KEY=hk_live_abcdefghij/);
  assert.match(r.text, /MealMate/);
});

// ─── advanced ops tools (read-only) ────────────────────────────────────

test("deploy_status: surfaces tenant status + live URL", async () => {
  const api = makeStubApi({
    "/api/ops/deploy-status/mealmate": {
      slug: "mealmate", status: "live",
      last_deploy_at: "2026-05-15T10:14:00Z",
      live_url: "https://mealmate.hatchik.com",
    },
  });
  const r = await deployStatusTool(api).handler({ slug: "mealmate" });
  assert.match(r.text, /Status: live/);
  assert.match(r.text, /mealmate\.hatchik\.com/);
});

test("preview_url: synthesises conventional URL", async () => {
  const api = makeStubApi({
    "/api/ops/preview-url/mealmate?branch=feat-x": {
      slug: "mealmate", branch: "feat-x",
      preview_url: "https://feat-x.mealmate.hatchik.com",
      note: "Preview URLs activate only for branches with a push-to-deploy hook.",
    },
  });
  const r = await previewUrlTool(api).handler({ slug: "mealmate", branch: "feat-x" });
  assert.match(r.text, /feat-x\.mealmate\.hatchik\.com/);
});

test("pending_migrations: lists files when any are queued", async () => {
  const api = makeStubApi({
    "/api/ops/pending-migrations/mealmate": {
      slug: "mealmate",
      pending: [
        { file: "003_add_orders.sql" }, { file: "004_index_email.sql" },
      ],
    },
  });
  const r = await pendingMigrationsTool(api).handler({ slug: "mealmate" });
  assert.match(r.text, /2 pending migration/);
  assert.match(r.text, /003_add_orders/);
});

test("pending_migrations: returns 'none' cleanly", async () => {
  const api = makeStubApi({
    "/api/ops/pending-migrations/mealmate": { slug: "mealmate", pending: [] },
  });
  const r = await pendingMigrationsTool(api).handler({ slug: "mealmate" });
  assert.match(r.text, /No pending migrations/);
});

test("read_logs: builds the querystring + shows the lines", async () => {
  const api = makeStubApi({
    "/api/ops/logs/mealmate?service=api&since=2h&lines=50": {
      slug: "mealmate", service: "api", since: "2h",
      lines: ["[INFO] booted", "[INFO] 200 GET /"],
    },
  });
  const r = await readLogsTool(api).handler({
    slug: "mealmate", service: "api", since: "2h", lines: 50,
  });
  assert.match(r.text, /booted/);
  assert.match(r.text, /200 GET/);
});

test("recent_errors: surfaces grep hits", async () => {
  const api = makeStubApi({
    "/api/ops/recent-errors/mealmate?since=24h": {
      slug: "mealmate", since: "24h",
      errors: [
        { container: "mealmate-api-1", line: "[ERROR] db connection refused" },
      ],
    },
  });
  const r = await recentErrorsTool(api).handler({ slug: "mealmate", since: "24h" });
  assert.match(r.text, /1 error/);
  assert.match(r.text, /connection refused/);
});

// ─── advanced ops tools (confirm-required) ─────────────────────────────

test("deploy_to_prod: returns the confirm URL and instructs the AI not to follow", async () => {
  const api = makeStubApi({
    "/api/ops/deploy-to-prod": {
      status: "pending_confirmation",
      summary: "Deploy branch 'main' of mealmate to production.",
      confirm_url: "https://hatchik.com/confirm/tc_xyz",
      token: "tc_xyz", expires_at: "2026-05-15T10:20:00Z",
      expires_in_seconds: 300,
    },
  });
  const r = await deployToProdTool(api).handler({ branch: "main" });
  assert.match(r.text, /tc_xyz/);
  assert.match(r.text, /Do NOT auto-follow/);
});

test("apply_migration: requires migration_file + emits confirm URL", async () => {
  const api = makeStubApi({
    "/api/ops/apply-migration": {
      status: "pending_confirmation",
      summary: "Apply migration 003.sql to the mealmate database.",
      confirm_url: "https://hatchik.com/confirm/tc_yyy",
      token: "tc_yyy", expires_at: "2026-05-15T10:20:00Z",
      expires_in_seconds: 300,
    },
  });
  const r = await applyMigrationTool(api).handler({ migration_file: "003.sql" });
  assert.match(r.text, /tc_yyy/);
  assert.match(r.text, /destructive action/);
});

test("rollback: stresses replaces-data in the summary", async () => {
  const api = makeStubApi({
    "/api/ops/rollback": {
      status: "pending_confirmation",
      summary: "Restore mealmate's database from snapshot snap_2026-05-14. Current data is replaced.",
      confirm_url: "https://hatchik.com/confirm/tc_zzz",
      token: "tc_zzz", expires_at: "2026-05-15T10:20:00Z",
      expires_in_seconds: 300,
    },
  });
  const r = await rollbackTool(api).handler({ snapshot_id: "snap_2026-05-14" });
  assert.match(r.text, /replaced/);
  assert.match(r.text, /tc_zzz/);
});

test("team_invite: confirm URL + role default", async () => {
  const api = makeStubApi({
    "/api/ops/team-invite": {
      status: "pending_confirmation",
      summary: "Invite alice@example.com to your Hatchik project as a 'developer'.",
      confirm_url: "https://hatchik.com/confirm/tc_aaa",
      token: "tc_aaa", expires_at: "2026-05-15T10:20:00Z",
      expires_in_seconds: 300,
    },
  });
  const r = await teamInviteTool(api).handler({ email: "alice@example.com" });
  assert.match(r.text, /alice@example\.com/);
  assert.match(r.text, /tc_aaa/);
});

test("cancel_subscription: hands back the Paddle portal URL", async () => {
  const api = makeStubApi({
    "/api/ops/cancel-subscription": {
      portal_url: "https://buyer.paddle.com/customer/cus_123",
      note: "Cancellation runs in Paddle's customer portal.",
    },
  });
  const r = await cancelSubscriptionTool(api).handler({});
  assert.match(r.text, /buyer\.paddle\.com/);
  assert.match(r.text, /Paddle/);
});

test("cancel_subscription: no portal_url → fallback to email", async () => {
  const api = makeStubApi({
    "/api/ops/cancel-subscription": { portal_url: null, note: "—" },
  });
  const r = await cancelSubscriptionTool(api).handler({});
  assert.match(r.text, /hello@hatchik\.com/);
});
