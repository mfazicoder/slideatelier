/**
 * services — "what's set up" inventory for one tenant.
 *
 * Maps to GET /api/account/services/{slug}. Returns the canonical
 * inventory (Postgres MB, auth providers, mail quota, mobile builds/hr,
 * etc.) that the customer dashboard's Services tab renders.
 */

import { z } from "zod";
import type { ApiClient } from "../api.js";
import { Tool } from "./types.js";

const InputSchema = z.object({
  slug: z.string().min(1),
});

interface ServicesResponse {
  slug: string;
  sandbox_url?: string | null;
  repo_url?: string | null;
  tier: string;
  version: string;
  wired: Array<{ name: string; detail?: string; quota?: string }>;
  available_on_upgrade?: Array<{
    tier: string;
    name: string;
    detail?: string;
  }>;
}

export function servicesTool(api: ApiClient): Tool {
  return {
    name: "services",
    description:
      "What's wired in a specific tenant: provisioned services, quotas, " +
      "and what's available on upgrade. Pass the slug from list_sandboxes.",
    inputSchema: {
      type: "object",
      properties: {
        slug: {
          type: "string",
          minLength: 1,
          description: "Sandbox / tenant slug (from list_sandboxes).",
        },
      },
      required: ["slug"],
      additionalProperties: false,
    },
    async handler(rawInput) {
      const input = InputSchema.parse(rawInput);
      const r = await api.get<ServicesResponse>(
        `/api/account/services/${encodeURIComponent(input.slug)}`,
      );
      const lines: string[] = [];
      lines.push(`${input.slug} — tier ${r.tier} (inventory v${r.version})`);
      if (r.sandbox_url) lines.push(`URL: ${r.sandbox_url}`);
      if (r.repo_url) lines.push(`Repo: ${r.repo_url}`);
      lines.push("");
      lines.push("Wired:");
      for (const w of r.wired) {
        lines.push(
          `  ✓ ${w.name}${w.detail ? ` — ${w.detail}` : ""}${w.quota ? ` (${w.quota})` : ""}`,
        );
      }
      if (r.available_on_upgrade && r.available_on_upgrade.length > 0) {
        lines.push("");
        lines.push("Available on upgrade:");
        for (const u of r.available_on_upgrade) {
          lines.push(`  → ${u.name} [${u.tier}]${u.detail ? ` — ${u.detail}` : ""}`);
        }
      }
      return { text: lines.join("\n"), data: r };
    },
  };
}
