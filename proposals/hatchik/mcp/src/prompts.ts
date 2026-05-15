/**
 * MCP prompts — slash commands the customer types in their AI tool.
 * Each prompt returns a message that primes the AI to start a workflow.
 *
 * Spec (mcp-signup-flow.md "Prompts"):
 *   /hatchik start                     — kick off a new signup
 *   /hatchik connect <project_id>      — set API key for an existing project
 *   /hatchik deploy                    — promote a branch to prod
 *   /hatchik help                      — overview
 *
 * The MCP SDK exposes these via prompts/list + prompts/get. AI clients
 * render them as autocompleted commands; clicking one prefills the chat
 * with the prompt's "messages" payload.
 */

import type { Config } from "./config.js";

export interface PromptMessage {
  role: "user" | "assistant";
  content: { type: "text"; text: string };
}

export interface Prompt {
  name: string;
  description: string;
  arguments?: Array<{
    name: string;
    description: string;
    required?: boolean;
  }>;
  /** Resolve the prompt into a list of messages for the AI. */
  build(args: Record<string, string>): PromptMessage[];
}

export function buildPrompts(config: Config): Prompt[] {
  const isSignup = config.mode === "signup";

  const start: Prompt = {
    name: "hatchik_start",
    description: "Start a new Hatchik signup, conversationally. Walks through name, domain, tier, and checkout.",
    build: () => [
      {
        role: "user",
        content: {
          type: "text",
          text:
            "Help me sign up for Hatchik. Use the Hatchik MCP tools — start with `start_signup` " +
            "and walk me through name, domain, tier choice, then quote and checkout. Ask me " +
            "questions one at a time. If I don't know what to pick for a field, suggest a default " +
            "and explain it. Show me the price before I pay.",
        },
      },
    ],
  };

  const connect: Prompt = {
    name: "hatchik_connect",
    description: "Tell the MCP to connect to an existing Hatchik project — useful when switching machines or onboarding a teammate.",
    arguments: [
      { name: "project_id", description: "The numeric project id (from /account or your welcome email).", required: false },
      { name: "api_key", description: "Your hk_live_* API key.", required: false },
    ],
    build: (args) => [
      {
        role: "user",
        content: {
          type: "text",
          text:
            (args.project_id
              ? `Connect this MCP to Hatchik project ${args.project_id}. `
              : "Connect this MCP to my Hatchik project. ") +
            "Tell me the exact MCP config block I need to paste — the file path " +
            "for the AI tool I'm in, and the env vars (HATCHIK_API_KEY, HATCHIK_API_URL). " +
            (args.api_key
              ? `My API key is ${args.api_key}.`
              : "Ask me for the hk_live_* key, walk me through where to find it, and explain that I'll need to restart the AI tool after editing the config."),
        },
      },
    ],
  };

  const deploy: Prompt = {
    name: "hatchik_deploy",
    description: "Promote a branch to production. The AI walks through the deploy + browser confirmation.",
    arguments: [
      { name: "branch", description: "Branch to deploy. Default 'main'.", required: false },
      { name: "slug", description: "Tenant slug if you have more than one.", required: false },
    ],
    build: (args) => [
      {
        role: "user",
        content: {
          type: "text",
          text:
            `Deploy${args.branch ? ` the ${args.branch} branch` : ""}${args.slug ? ` of ${args.slug}` : ""} to production using Hatchik. ` +
            "Use the deploy_to_prod tool. It will return a confirmation URL — present it to me " +
            "as a clickable link with a one-line summary, never auto-follow it. After I confirm in " +
            "my browser, check status until the deploy is live.",
        },
      },
    ],
  };

  const help: Prompt = {
    name: "hatchik_help",
    description: "Brief overview of every Hatchik MCP command and resource.",
    build: () => [
      {
        role: "user",
        content: {
          type: "text",
          text:
            isSignup
              ? "Show me what the Hatchik MCP can do. I haven't signed up yet — list the eight " +
                "signup tools and what each does, in one sentence each."
              : "Show me what the Hatchik MCP can do. I'm already signed in — list the ops " +
                "tools (read-only + browser-confirmed), the resources (hatchik://...), and the " +
                "slash commands. Group by 'safe to call from chat' vs 'requires browser confirmation'.",
        },
      },
    ],
  };

  return [start, connect, deploy, help];
}
