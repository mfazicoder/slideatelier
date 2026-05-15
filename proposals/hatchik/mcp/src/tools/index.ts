/**
 * Tool registry — every tool the MCP exposes lives in its own file.
 * This module assembles them into a single list keyed by name so the
 * server can register them in one pass.
 *
 * Per the design (mcp-signup-flow.md), tools split into two modes:
 *
 *   signup-mode  → available when HATCHIK_API_KEY is NOT set:
 *                    start_signup, suggest_domains, check_domain,
 *                    set_choices, quote, checkout, status, complete
 *
 *   ops-mode     → available when HATCHIK_API_KEY IS set:
 *                    project_info, list_sandboxes, services,
 *                    mobile_builds_list, mobile_build_trigger
 *
 * The signup tools speak to the wizard_sessions endpoints
 * (POST /api/wizard/sessions, PATCH /api/wizard/sessions/{id}, etc.)
 * which mint a hk_live_* api key on completion. The MCP then asks the
 * customer to paste that key into their AI tool's MCP config and
 * restart, switching into ops mode.
 */

import type { Config } from "../config.js";
import type { ApiClient } from "../api.js";
import { projectInfoTool } from "./project-info.js";
import { listSandboxesTool } from "./list-sandboxes.js";
import { servicesTool } from "./services.js";
import { mobileBuildsListTool, mobileBuildTriggerTool } from "./mobile-builds.js";
import { buildSignupTools } from "./signup.js";
import type { Tool } from "./types.js";

export function buildToolList(config: Config, api: ApiClient): Tool[] {
  if (config.mode === "ops") {
    return [
      projectInfoTool(api),
      listSandboxesTool(api),
      servicesTool(api),
      mobileBuildsListTool(api),
      mobileBuildTriggerTool(api),
    ];
  }
  // signup mode: the eight conversational signup tools.
  return buildSignupTools(api);
}

export type { Tool } from "./types.js";
