/**
 * Signup-mode tool stubs.
 *
 * Per mcp-signup-flow.md the full signup-mode surface is:
 *   start_signup, suggest_domains, check_domain, set_choices,
 *   quote, checkout, status, complete.
 *
 * The backend wizard-session endpoints these would call don't exist yet
 * on signup-service. Rather than expose nothing in signup-mode (which
 * would confuse the AI), we expose a single `start_signup` stub that
 * tells the AI to redirect the human to https://hatchik.com/start —
 * the website wizard is the canonical signup path until the MCP signup
 * backend ships.
 *
 * Once the wizard-session endpoints are built (planned: POST /api/wizard/sessions,
 * GET /api/wizard/sessions/{id}/quote, etc.) we replace this stub with
 * the real tools and they fall through to the canonical pipeline.
 */

import type { ApiClient } from "../api.js";
import { EMPTY_SCHEMA, Tool } from "./types.js";

export function startSignupStub(_api: ApiClient): Tool {
  return {
    name: "start_signup",
    description:
      "Start a Hatchik signup. CURRENTLY: returns a link to the web wizard " +
      "at https://hatchik.com/start — the in-chat MCP signup flow is in build. " +
      "Once it ships, this tool will drive the whole signup conversationally.",
    inputSchema: EMPTY_SCHEMA,
    async handler(_input) {
      return {
        text:
          "The Hatchik in-chat signup MCP flow isn't live yet. " +
          "Open https://hatchik.com/start in your browser to sign up. " +
          "Once signed in, copy the API key from your account page into the " +
          "MCP config's HATCHIK_API_KEY env var and reload this server — " +
          "the ops-mode tools (project_info, services, mobile_builds, etc.) " +
          "will then be available without leaving chat.",
        data: { signup_url: "https://hatchik.com/start", mode: "signup" },
      };
    },
  };
}
