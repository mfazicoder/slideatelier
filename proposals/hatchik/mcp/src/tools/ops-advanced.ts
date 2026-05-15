/**
 * Advanced ops-mode tools — the second half of the MCP surface from
 * mcp-signup-flow.md.
 *
 * Two groups:
 *
 *   Read-only (execute server-side, return data inline):
 *     deploy_status, preview_url, pending_migrations, read_logs,
 *     recent_errors
 *
 *   Confirm-required (return a confirm_url; customer clicks Yes in
 *   browser; backend then executes):
 *     deploy_to_prod, apply_migration, rollback, team_invite,
 *     cancel_subscription (no token — goes to Paddle portal)
 *
 * The browser-confirmation pattern is enforced both here (we return the
 * URL rather than the result) AND server-side (the action endpoints
 * refuse to execute without a valid confirmation token). Defence in
 * depth — see proposals/hatchik/signup-service/confirmations.py.
 */

import type { ApiClient } from "../api.js";
import { Tool } from "./types.js";

// Used by every confirm-required tool's response so the AI knows what
// to do with the URL.
const CONFIRM_INSTRUCTIONS = (action: string, summary: string, url: string, ttl: number) =>
  `${summary}\n\nThis is a destructive action — Hatchik does NOT execute it from chat alone.\n` +
  `Show the customer this link and ask them to click it in their browser:\n\n` +
  `  ${url}\n\n` +
  `Token expires in ${ttl}s. The customer's browser will display the action plainly with Yes/Cancel buttons. ` +
  `Do NOT auto-follow the link yourself.\n\n` +
  `After the customer clicks Yes, you can poll /api/confirmations/{token} (or just ask them to confirm verbally) to see the outcome.`;

// ─── Read-only tools ─────────────────────────────────────────────────────
export function deployStatusTool(api: ApiClient): Tool {
  return {
    name: "deploy_status",
    description:
      "Show the live deploy state for a tenant: status, last_seen_at, " +
      "last_deploy_at, and live URL. Read-only.",
    inputSchema: {
      type: "object",
      properties: {
        slug: { type: "string", minLength: 1, maxLength: 60, description: "Tenant slug." },
      },
      required: ["slug"],
      additionalProperties: false,
    },
    async handler(input) {
      const { slug } = input as { slug: string };
      const r = await api.get<{
        slug: string; status: string;
        last_seen_at?: string; last_deploy_at?: string;
        live_url?: string;
      }>(`/api/ops/deploy-status/${encodeURIComponent(slug)}`);
      const lines = [
        `Tenant: ${r.slug}`,
        `Status: ${r.status}`,
      ];
      if (r.live_url) lines.push(`URL: ${r.live_url}`);
      if (r.last_deploy_at) lines.push(`Last deploy: ${r.last_deploy_at}`);
      if (r.last_seen_at) lines.push(`Last seen: ${r.last_seen_at}`);
      return { text: lines.join("\n"), data: r };
    },
  };
}

export function previewUrlTool(api: ApiClient): Tool {
  return {
    name: "preview_url",
    description:
      "Return the preview URL for a branch deploy of a tenant. Conventional " +
      "shape: https://<branch>.<slug>.hatchik.com. The branch must have an " +
      "active push-to-deploy hook to actually resolve.",
    inputSchema: {
      type: "object",
      properties: {
        slug: { type: "string", minLength: 1, maxLength: 60 },
        branch: { type: "string", minLength: 1, maxLength: 80 },
      },
      required: ["slug", "branch"],
      additionalProperties: false,
    },
    async handler(input) {
      const { slug, branch } = input as { slug: string; branch: string };
      const r = await api.get<{
        slug: string; branch: string; preview_url: string; note: string;
      }>(`/api/ops/preview-url/${encodeURIComponent(slug)}?branch=${encodeURIComponent(branch)}`);
      return {
        text: `Preview URL for ${r.slug}@${r.branch}:\n  ${r.preview_url}\n\n${r.note}`,
        data: r,
      };
    },
  };
}

export function pendingMigrationsTool(api: ApiClient): Tool {
  return {
    name: "pending_migrations",
    description:
      "List migration files queued for the tenant's database. Read-only — " +
      "use apply_migration to actually run one (browser confirms).",
    inputSchema: {
      type: "object",
      properties: { slug: { type: "string", minLength: 1, maxLength: 60 } },
      required: ["slug"],
      additionalProperties: false,
    },
    async handler(input) {
      const { slug } = input as { slug: string };
      const r = await api.get<{
        slug: string; pending: Array<{ file: string }>;
      }>(`/api/ops/pending-migrations/${encodeURIComponent(slug)}`);
      if (r.pending.length === 0) {
        return { text: `No pending migrations for ${slug}.`, data: r };
      }
      const lines = [`${r.pending.length} pending migration(s) for ${slug}:`, ""];
      for (const p of r.pending) lines.push(`  • ${p.file}`);
      lines.push("");
      lines.push("Run one with apply_migration({slug, migration_file}). The customer confirms in their browser.");
      return { text: lines.join("\n"), data: r };
    },
  };
}

export function readLogsTool(api: ApiClient): Tool {
  return {
    name: "read_logs",
    description:
      "Return recent container logs for a tenant service. service defaults " +
      "to 'web'; since accepts docker-style durations (e.g. '1h', '15m'); " +
      "lines caps at 1000.",
    inputSchema: {
      type: "object",
      properties: {
        slug: { type: "string", minLength: 1, maxLength: 60 },
        service: { type: "string", maxLength: 30,
                   description: "Service name (web, api, worker, postgres, ...). Default 'web'." },
        since: { type: "string", maxLength: 20,
                 description: "How far back, e.g. '1h', '30m'. Default '1h'." },
        lines: { type: "integer", minimum: 1, maximum: 1000,
                 description: "Max lines to return. Default 200." },
      },
      required: ["slug"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { slug: string; service?: string; since?: string; lines?: number };
      const q = new URLSearchParams();
      if (args.service) q.set("service", args.service);
      if (args.since) q.set("since", args.since);
      if (args.lines) q.set("lines", String(args.lines));
      const r = await api.get<{
        slug: string; service: string; since: string; lines: string[];
      }>(`/api/ops/logs/${encodeURIComponent(args.slug)}?${q}`);
      const header = `Logs: ${r.slug}/${r.service} (since ${r.since}) — ${r.lines.length} lines`;
      const body = r.lines.length === 0 ? "(no log lines in window)" : r.lines.join("\n");
      return { text: `${header}\n${"-".repeat(header.length)}\n${body}`, data: r };
    },
  };
}

export function recentErrorsTool(api: ApiClient): Tool {
  return {
    name: "recent_errors",
    description:
      "Scan recent logs across all services for ERROR / FATAL / Traceback. " +
      "Summary view; pair with read_logs for the full context of any one " +
      "incident.",
    inputSchema: {
      type: "object",
      properties: {
        slug: { type: "string", minLength: 1, maxLength: 60 },
        since: { type: "string", maxLength: 20, description: "Default '24h'." },
      },
      required: ["slug"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { slug: string; since?: string };
      const q = args.since ? `?since=${encodeURIComponent(args.since)}` : "";
      const r = await api.get<{
        slug: string; since: string;
        errors: Array<{ container: string; line: string }>;
      }>(`/api/ops/recent-errors/${encodeURIComponent(args.slug)}${q}`);
      if (r.errors.length === 0) {
        return { text: `No errors in ${r.slug} logs (since ${r.since}).`, data: r };
      }
      const lines = [`${r.errors.length} error/fatal line(s) in ${r.slug} (since ${r.since}):`, ""];
      for (const e of r.errors.slice(0, 25)) {
        lines.push(`  [${e.container}] ${e.line.slice(0, 240)}`);
      }
      if (r.errors.length > 25) lines.push(`  … and ${r.errors.length - 25} more.`);
      return { text: lines.join("\n"), data: r };
    },
  };
}

// ─── Confirm-required tools ──────────────────────────────────────────────
interface ConfirmResponse {
  status: "pending_confirmation";
  summary: string;
  confirm_url: string;
  token: string;
  expires_at: string;
  expires_in_seconds: number;
}

export function deployToProdTool(api: ApiClient): Tool {
  return {
    name: "deploy_to_prod",
    description:
      "Promote a branch to production. Returns a confirmation URL — the " +
      "customer must click Yes in their browser before the deploy fires.",
    inputSchema: {
      type: "object",
      properties: {
        slug: { type: "string", maxLength: 60 },
        branch: { type: "string", minLength: 1, maxLength: 80, description: "Default 'main'." },
      },
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { slug?: string; branch?: string };
      const r = await api.post<ConfirmResponse>("/api/ops/deploy-to-prod", {
        slug: args.slug, branch: args.branch || "main",
      });
      return {
        text: CONFIRM_INSTRUCTIONS("deploy_to_prod", r.summary, r.confirm_url, r.expires_in_seconds),
        data: r,
      };
    },
  };
}

export function applyMigrationTool(api: ApiClient): Tool {
  return {
    name: "apply_migration",
    description:
      "Apply a queued migration to the tenant database. ALWAYS returns a " +
      "confirmation URL — never runs from chat alone. Use pending_migrations " +
      "first to discover the available migration_file values.",
    inputSchema: {
      type: "object",
      properties: {
        slug: { type: "string", maxLength: 60 },
        migration_file: {
          type: "string", minLength: 1, maxLength: 200,
          description: "Filename from pending_migrations, e.g. '003_add_orders_table.sql'.",
        },
      },
      required: ["migration_file"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { slug?: string; migration_file: string };
      const r = await api.post<ConfirmResponse>("/api/ops/apply-migration", {
        slug: args.slug, migration_file: args.migration_file,
      });
      return {
        text: CONFIRM_INSTRUCTIONS("apply_migration", r.summary, r.confirm_url, r.expires_in_seconds),
        data: r,
      };
    },
  };
}

export function rollbackTool(api: ApiClient): Tool {
  return {
    name: "rollback",
    description:
      "Restore the tenant database from a snapshot. ALWAYS returns a " +
      "confirmation URL — restoring REPLACES current data, so this is " +
      "browser-confirmed every time.",
    inputSchema: {
      type: "object",
      properties: {
        slug: { type: "string", maxLength: 60 },
        snapshot_id: {
          type: "string", minLength: 1, maxLength: 80,
          description: "Snapshot id from the snapshots resource or list.",
        },
      },
      required: ["snapshot_id"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { slug?: string; snapshot_id: string };
      const r = await api.post<ConfirmResponse>("/api/ops/rollback", {
        slug: args.slug, snapshot_id: args.snapshot_id,
      });
      return {
        text: CONFIRM_INSTRUCTIONS("rollback", r.summary, r.confirm_url, r.expires_in_seconds),
        data: r,
      };
    },
  };
}

export function teamInviteTool(api: ApiClient): Tool {
  return {
    name: "team_invite",
    description:
      "Invite a teammate to the Hatchik project. Browser confirms — auth " +
      "boundary doesn't flow through chat. role defaults to 'developer'.",
    inputSchema: {
      type: "object",
      properties: {
        email: {
          type: "string", format: "email",
          description: "The teammate's email.",
        },
        role: {
          type: "string", enum: ["developer", "viewer", "admin"],
          description: "Default 'developer'.",
        },
      },
      required: ["email"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { email: string; role?: string };
      const r = await api.post<ConfirmResponse>("/api/ops/team-invite", {
        email: args.email, role: args.role || "developer",
      });
      return {
        text: CONFIRM_INSTRUCTIONS("team_invite", r.summary, r.confirm_url, r.expires_in_seconds),
        data: r,
      };
    },
  };
}

export function cancelSubscriptionTool(api: ApiClient): Tool {
  return {
    name: "cancel_subscription",
    description:
      "Return the Paddle customer-portal URL where the customer cancels " +
      "their subscription. Paddle owns the cancel flow (PCI + dispute), " +
      "so there's no Hatchik-side confirmation token — just hand off the URL.",
    inputSchema: { type: "object", properties: {}, additionalProperties: false },
    async handler(_input) {
      const r = await api.get<{ portal_url: string | null; note: string }>(
        "/api/ops/cancel-subscription",
      );
      if (!r.portal_url) {
        return {
          text:
            "We couldn't resolve your Paddle customer record. " +
            "Email hello@hatchik.com and we'll cancel manually within 1 business day.",
          data: r,
        };
      }
      return {
        text:
          `Open this in your browser to cancel:\n\n  ${r.portal_url}\n\n` + r.note,
        data: r,
      };
    },
  };
}


// ─── Registry export ─────────────────────────────────────────────────────
export function buildOpsAdvancedTools(api: ApiClient): Tool[] {
  return [
    deployStatusTool(api),
    previewUrlTool(api),
    pendingMigrationsTool(api),
    readLogsTool(api),
    recentErrorsTool(api),
    deployToProdTool(api),
    applyMigrationTool(api),
    rollbackTool(api),
    teamInviteTool(api),
    cancelSubscriptionTool(api),
  ];
}
