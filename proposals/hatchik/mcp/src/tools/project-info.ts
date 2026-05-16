/**
 * project_info — returns the customer's basic account state.
 *
 * Maps to GET /api/account/me on signup-service. Used by the AI to ground
 * itself on which Hatchik account it's connected to before doing other work.
 */

import type { ApiClient } from "../api.js";
import { EMPTY_SCHEMA, Tool } from "./types.js";

interface AccountMeResponse {
  email: string;
  first_name?: string | null;
  github_username?: string | null;
  sandboxes: Array<{
    slug: string;
    product_name?: string | null;
    status: string;
    url?: string | null;
    repo_url?: string | null;
    tier?: string | null;
  }>;
}

export function projectInfoTool(api: ApiClient): Tool {
  return {
    name: "project_info",
    description:
      "Return the signed-in Hatchik account: email, GitHub handle, " +
      "and every sandbox/launch tenant owned. Call this first to ground " +
      "yourself on what the customer has before doing anything else.",
    inputSchema: EMPTY_SCHEMA,
    async handler(_input) {
      const me = await api.get<AccountMeResponse>("/api/account/me");
      const lines: string[] = [];
      lines.push(`Account: ${me.email}${me.first_name ? ` (${me.first_name})` : ""}`);
      if (me.github_username) {
        lines.push(`GitHub: @${me.github_username}`);
      }
      const live = me.sandboxes.filter((s) => s.status !== "decommissioned");
      if (live.length === 0) {
        lines.push("No active sandboxes yet.");
      } else {
        lines.push(`${live.length} active tenant${live.length === 1 ? "" : "s"}:`);
        for (const s of live) {
          const tier = s.tier ? ` [${s.tier}]` : "";
          const url = s.url ? ` → ${s.url}` : "";
          lines.push(`  • ${s.slug}${tier} (${s.status})${url}`);
        }
      }
      return { text: lines.join("\n"), data: me };
    },
  };
}
