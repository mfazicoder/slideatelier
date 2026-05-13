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
 *                    project_info, list_sandboxes, redeploy_status,
 *                    redeploy, mobile_builds_list, mobile_build_trigger,
 *                    services, github_invite
 *
 * Phase 1 (this commit): ops-mode tools that map 1:1 to existing
 * signup-service endpoints. Signup-mode tools are scaffolded as stubs
 * that return "not yet implemented" with a clear pointer to the design
 * doc — keeps the surface area discoverable while we build the backend
 * wizard sessions out.
 */

import type { Config } from "../config.js";
import type { ApiClient } from "../api.js";
import { projectInfoTool } from "./project-info.js";
import { listSandboxesTool } from "./list-sandboxes.js";
import { servicesTool } from "./services.js";
import { mobileBuildsListTool, mobileBuildTriggerTool } from "./mobile-builds.js";
import { startSignupStub } from "./signup-stubs.js";
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
  // signup mode: only the signup-flow tools are exposed.
  return [startSignupStub(api)];
}

export type { Tool } from "./types.js";
