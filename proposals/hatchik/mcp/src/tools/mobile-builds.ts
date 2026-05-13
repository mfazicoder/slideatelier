/**
 * mobile_builds_list  — recent iOS/Android build runs for a tenant.
 * mobile_build_trigger — kick off a fresh build (rate-limited to
 *                        MOBILE_BUILD_RATE_LIMIT_MAX/hour server-side).
 *
 * Maps to:
 *   GET  /api/account/mobile-builds/{slug}
 *   POST /api/account/mobile-builds/{slug}/trigger
 *
 * Trigger is a "soft" action — it dispatches a GitHub Actions workflow,
 * doesn't directly affect customer data. Safe to expose to the AI
 * without browser-confirmation gating.
 */

import { z } from "zod";
import type { ApiClient } from "../api.js";
import { Tool } from "./types.js";

const SlugOnly = z.object({ slug: z.string().min(1) });

const TriggerInput = z.object({
  slug: z.string().min(1),
  platforms: z
    .enum(["ios", "android", "both"])
    .default("both"),
});

interface MobileBuild {
  id: number;
  status: string; // "queued" | "in_progress" | "success" | "failure"
  platforms: string;
  created_at: string;
  ios_artifact_url?: string | null;
  android_artifact_url?: string | null;
  workflow_url?: string | null;
}

export function mobileBuildsListTool(api: ApiClient): Tool {
  return {
    name: "mobile_builds_list",
    description:
      "Recent iOS + Android build runs for a tenant. Shows status, " +
      "queued/in_progress/success/failure, plus download URLs once a " +
      "build finishes (~8-15 minutes).",
    inputSchema: {
      type: "object",
      properties: {
        slug: { type: "string", minLength: 1, description: "Sandbox / tenant slug." },
      },
      required: ["slug"],
      additionalProperties: false,
    },
    async handler(rawInput) {
      const input = SlugOnly.parse(rawInput);
      const r = await api.get<{ builds: MobileBuild[] }>(
        `/api/account/mobile-builds/${encodeURIComponent(input.slug)}`,
      );
      if (!r.builds || r.builds.length === 0) {
        return {
          text: `No mobile builds yet for ${input.slug}. Call mobile_build_trigger to start one.`,
          data: r,
        };
      }
      const lines = r.builds.map((b) => {
        const icon =
          b.status === "success"
            ? "✓"
            : b.status === "failure"
              ? "✗"
              : b.status === "in_progress"
                ? "…"
                : "•";
        const artefacts =
          b.status === "success"
            ? [
                b.ios_artifact_url ? `ios: ${b.ios_artifact_url}` : null,
                b.android_artifact_url ? `android: ${b.android_artifact_url}` : null,
              ]
                .filter(Boolean)
                .join(" · ")
            : "";
        return (
          `${icon} #${b.id} [${b.platforms}] ${b.status} — ${b.created_at}` +
          (artefacts ? `\n    ${artefacts}` : "")
        );
      });
      return {
        text: `${r.builds.length} build${r.builds.length === 1 ? "" : "s"}:\n${lines.join("\n")}`,
        data: r,
      };
    },
  };
}

export function mobileBuildTriggerTool(api: ApiClient): Tool {
  return {
    name: "mobile_build_trigger",
    description:
      "Start a fresh iOS + Android build for a tenant. Returns immediately; " +
      "actual builds take 8-15 minutes. Rate-limited to 3 builds/hour per " +
      "tenant — exceeding returns a 429 with a clear retry-after.",
    inputSchema: {
      type: "object",
      properties: {
        slug: { type: "string", minLength: 1, description: "Sandbox / tenant slug." },
        platforms: {
          type: "string",
          enum: ["ios", "android", "both"],
          default: "both",
          description: "Which platforms to build. Default 'both'.",
        },
      },
      required: ["slug"],
      additionalProperties: false,
    },
    async handler(rawInput) {
      const input = TriggerInput.parse(rawInput);
      const r = await api.post<{ ok: boolean; message: string }>(
        `/api/account/mobile-builds/${encodeURIComponent(input.slug)}/trigger`,
        { platforms: input.platforms },
      );
      return {
        text: `${r.ok ? "✓" : "✗"} ${r.message}`,
        data: r,
      };
    },
  };
}
