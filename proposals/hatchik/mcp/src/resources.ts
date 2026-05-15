/**
 * MCP resources — read-only data the AI can pull into context without
 * an explicit tool call. The MCP client offers these to the AI when
 * the conversation seems relevant (Cursor: "Read resource" picker;
 * Claude Desktop: model autonomously fetches when grounding).
 *
 * Spec (mcp-signup-flow.md "Resources"):
 *   hatchik://project              — project metadata (ops mode only)
 *   hatchik://deploys/recent       — last 20 deploys with status
 *   hatchik://migrations/pending   — pending migration details
 *   hatchik://snapshots            — restorable nightly snapshots
 *
 * Each resource is implemented as a small fetch+format call to the
 * existing signup-service endpoints we already built.
 */

import type { ApiClient } from "./api.js";

export interface Resource {
  uri: string;
  name: string;
  description: string;
  mimeType: string;
  /** Returns the resource body as a string (JSON or text). */
  read(): Promise<string>;
}

function makeResources(api: ApiClient): Resource[] {
  return [
    {
      uri: "hatchik://project",
      name: "Hatchik project",
      description: "Account email, GitHub handle, and every active tenant.",
      mimeType: "application/json",
      async read() {
        const me = await api.get<unknown>("/api/account/me");
        return JSON.stringify(me, null, 2);
      },
    },
    {
      uri: "hatchik://deploys/recent",
      name: "Recent deploys",
      description: "Most recent push-to-deploy status across every tenant.",
      mimeType: "application/json",
      async read() {
        const me = await api.get<{ sandboxes: Array<{ slug: string }> }>(
          "/api/account/me",
        );
        const out: Array<{ slug: string; status?: unknown }> = [];
        for (const s of me.sandboxes ?? []) {
          try {
            const status = await api.get<unknown>(
              `/api/ops/deploy-status/${encodeURIComponent(s.slug)}`,
            );
            out.push({ slug: s.slug, status });
          } catch (e) {
            out.push({ slug: s.slug, status: { error: String(e) } });
          }
        }
        return JSON.stringify(out, null, 2);
      },
    },
    {
      uri: "hatchik://migrations/pending",
      name: "Pending migrations",
      description:
        "Migration files queued for each tenant — what apply_migration could run.",
      mimeType: "application/json",
      async read() {
        const me = await api.get<{ sandboxes: Array<{ slug: string }> }>(
          "/api/account/me",
        );
        const out: Array<{ slug: string; pending?: unknown }> = [];
        for (const s of me.sandboxes ?? []) {
          try {
            const r = await api.get<{ pending: unknown }>(
              `/api/ops/pending-migrations/${encodeURIComponent(s.slug)}`,
            );
            out.push({ slug: s.slug, pending: r.pending });
          } catch (e) {
            out.push({ slug: s.slug, pending: { error: String(e) } });
          }
        }
        return JSON.stringify(out, null, 2);
      },
    },
    {
      uri: "hatchik://snapshots",
      name: "Restorable snapshots",
      description:
        "Nightly database snapshots eligible for rollback. Pass snapshot_id to the rollback tool.",
      mimeType: "application/json",
      async read() {
        const me = await api.get<{ sandboxes: Array<{ slug: string }> }>(
          "/api/account/me",
        );
        const out: Array<{ slug: string; snapshots?: unknown }> = [];
        for (const s of me.sandboxes ?? []) {
          try {
            const r = await api.get<{ snapshots: unknown }>(
              `/api/ops/snapshots/${encodeURIComponent(s.slug)}`,
            );
            out.push({ slug: s.slug, snapshots: r.snapshots });
          } catch (e) {
            out.push({ slug: s.slug, snapshots: { error: String(e) } });
          }
        }
        return JSON.stringify(out, null, 2);
      },
    },
  ];
}

export function buildResources(api: ApiClient): Resource[] {
  return makeResources(api);
}
