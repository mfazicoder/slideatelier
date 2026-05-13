/**
 * list_sandboxes — narrower than project_info; returns just the tenant
 * list. Useful when the AI needs to iterate over sandboxes without the
 * account/identity preamble noise.
 *
 * Like project_info this maps to GET /api/account/me and pulls the
 * sandboxes array out. (We don't have a separate /api/account/sandboxes
 * endpoint on signup-service yet — when one exists, switch the path
 * here without changing the tool contract.)
 */

import type { ApiClient } from "../api.js";
import { EMPTY_SCHEMA, Tool } from "./types.js";

interface AccountMeResponse {
  sandboxes: Array<{
    slug: string;
    product_name?: string | null;
    status: string;
    tier?: string | null;
    url?: string | null;
    repo_url?: string | null;
    created_at?: string | null;
  }>;
}

export function listSandboxesTool(api: ApiClient): Tool {
  return {
    name: "list_sandboxes",
    description:
      "List every sandbox / launch tenant on the signed-in account. " +
      "Returns slug, product name, status, tier, URL, repo URL, and " +
      "creation date. Use to find a slug to pass to other tools.",
    inputSchema: EMPTY_SCHEMA,
    async handler(_input) {
      const me = await api.get<AccountMeResponse>("/api/account/me");
      const sandboxes = me.sandboxes ?? [];
      if (sandboxes.length === 0) {
        return {
          text: "No sandboxes yet. Sign up at https://hatchik.com to create one.",
          data: { sandboxes: [] },
        };
      }
      const lines = sandboxes.map((s) => {
        const tier = s.tier ?? "sandbox";
        const product = s.product_name ?? "(no product name)";
        return (
          `• ${s.slug} — ${product} [${tier}, ${s.status}]` +
          (s.url ? `\n    url: ${s.url}` : "") +
          (s.repo_url ? `\n    repo: ${s.repo_url}` : "")
        );
      });
      return {
        text: `${sandboxes.length} tenant${sandboxes.length === 1 ? "" : "s"}:\n${lines.join("\n")}`,
        data: { sandboxes },
      };
    },
  };
}
