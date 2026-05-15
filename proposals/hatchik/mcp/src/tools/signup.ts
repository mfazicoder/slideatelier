/**
 * Conversational signup tools — the eight steps from mcp-signup-flow.md.
 *
 * State lives server-side as a wizard_session (signups.db). The MCP is a
 * thin adapter: every tool takes a session_id (except start_signup which
 * creates one) and the backend tracks the cumulative choices.
 *
 * Flow the AI is meant to drive:
 *
 *   1. start_signup({description, product_name?})         → session_id
 *   2. suggest_domains({session_id, base_name})           → list
 *   3. set_choices({session_id, choices: {domain, email, ...}})
 *   4. quote({session_id})                                → £ breakdown
 *   5. checkout({session_id})                             → Paddle URL
 *   6. (customer pays in browser; webhook starts provisioning)
 *   7. status({session_id}) called periodically            → 'ready'
 *   8. complete({session_id, install_token})              → api_key
 *
 * Sandbox tier is free: checkout({session_id}) for tier=sandbox skips
 * Paddle and returns the install_token immediately. The AI can then
 * call status to poll provisioning + complete.
 */

import type { ApiClient } from "../api.js";
import { Tool } from "./types.js";

// ─── Shared schemas / helpers ─────────────────────────────────────────────
const SESSION_ID_SCHEMA = {
  type: "string",
  minLength: 8,
  maxLength: 40,
  pattern: "^ws_[A-Za-z0-9]+$",
  description: "Wizard session id returned by start_signup.",
};

function gbp(pence: number): string {
  return `£${(pence / 100).toFixed(2)}`;
}

// ─── 1. start_signup ──────────────────────────────────────────────────────
export function startSignupTool(api: ApiClient): Tool {
  return {
    name: "start_signup",
    description:
      "Open a new Hatchik signup session. Pass a one-line product description " +
      "(e.g. 'a meal-prep app for personal trainers'). Returns a session_id " +
      "that every subsequent signup tool needs. Optional product_name lets " +
      "you skip the naming step; otherwise we'll derive one.",
    inputSchema: {
      type: "object",
      properties: {
        description: {
          type: "string", minLength: 1, maxLength: 2000,
          description: "What the customer's product is. One sentence.",
        },
        product_name: {
          type: "string", minLength: 1, maxLength: 120,
          description: "Optional name for the product. If omitted, suggest one.",
        },
      },
      required: ["description"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { description: string; product_name?: string };
      const res = await api.post<{
        session_id: string;
        status: string;
        expires_at: string;
        choices: Record<string, unknown>;
      }>("/api/wizard/sessions", {
        description: args.description,
        product_name: args.product_name ?? "",
      });
      const expires = new Date(res.expires_at).toUTCString();
      const lines = [
        `Signup session opened.`,
        `  session_id: ${res.session_id}`,
        `  status: ${res.status}`,
        `  expires: ${expires}`,
        ``,
        `Next: call suggest_domains({session_id, base_name}) with the ` +
          `product name. If the customer hasn't given a name yet, ask them ` +
          `or propose one based on the description.`,
      ];
      return { text: lines.join("\n"), data: res };
    },
  };
}

// ─── 2. suggest_domains ───────────────────────────────────────────────────
export function suggestDomainsTool(api: ApiClient): Tool {
  return {
    name: "suggest_domains",
    description:
      "Suggest available domain names based on a base word. Returns up to " +
      "`count` candidates with their year-1 price in GBP. Use this after " +
      "start_signup to give the customer a shortlist to pick from.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: SESSION_ID_SCHEMA,
        base_name: {
          type: "string", minLength: 1, maxLength: 80,
          description: "Word to use as the domain root (lowercased + cleaned).",
        },
        tlds: {
          type: "array", items: { type: "string" }, maxItems: 8,
          description: "TLDs to try, e.g. ['.com', '.app']. Defaults to a sensible mix.",
        },
        count: {
          type: "integer", minimum: 1, maximum: 20,
          description: "Maximum number of suggestions to return. Default 6.",
        },
      },
      required: ["session_id", "base_name"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as {
        session_id: string; base_name: string;
        tlds?: string[]; count?: number;
      };
      const res = await api.post<{
        session_id: string; base_name: string;
        suggestions: Array<{
          domain: string; available: boolean; price_pence: number;
          premium: boolean; coverage_pence: number; customer_pence: number;
        }>;
      }>(
        `/api/wizard/sessions/${encodeURIComponent(args.session_id)}/suggest-domains`,
        { base_name: args.base_name, tlds: args.tlds, count: args.count },
      );
      const lines = [
        `Domain shortlist for "${res.base_name}":`,
        ``,
      ];
      for (const s of res.suggestions) {
        const tick = s.available ? "✓" : "✗";
        let cost = "free year-1";
        if (s.customer_pence > 0) {
          cost = `+${gbp(s.customer_pence)} year-1 (premium TLD, you cover the bit above £14)`;
        }
        const tag = s.premium ? " [premium TLD]" : "";
        lines.push(`  ${tick} ${s.domain}${tag} — ${cost}`);
      }
      lines.push("");
      lines.push(
        "Available domains have a tick. To lock one in, call " +
        "set_choices({session_id, choices: {domain: '<chosen>'}}).",
      );
      return { text: lines.join("\n"), data: res };
    },
  };
}

// ─── 3. check_domain ──────────────────────────────────────────────────────
export function checkDomainTool(api: ApiClient): Tool {
  return {
    name: "check_domain",
    description:
      "Check whether a specific domain is available + how much it costs " +
      "year-1. Useful when the customer types a specific domain rather than " +
      "picking from the suggest_domains shortlist.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: SESSION_ID_SCHEMA,
        domain: {
          type: "string", minLength: 4, maxLength: 255,
          description: "Fully qualified domain, e.g. 'mealmate.app'.",
        },
      },
      required: ["session_id", "domain"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { session_id: string; domain: string };
      const sid = encodeURIComponent(args.session_id);
      const dom = encodeURIComponent(args.domain);
      const res = await api.get<{
        domain: string; available: boolean; price_pence: number;
        premium: boolean; coverage_pence: number; customer_pence: number;
      }>(`/api/wizard/sessions/${sid}/check-domain?domain=${dom}`);
      const tick = res.available ? "available" : "not available";
      const cost = res.customer_pence > 0
        ? `${gbp(res.coverage_pence)} included + ${gbp(res.customer_pence)} you cover (premium TLD)`
        : `included in setup (£14/yr ceiling)`;
      return {
        text: `${res.domain}: ${tick}. Year-1 cost: ${cost}.`,
        data: res,
      };
    },
  };
}

// ─── 4. set_choices ───────────────────────────────────────────────────────
export function setChoicesTool(api: ApiClient): Tool {
  return {
    name: "set_choices",
    description:
      "Record the customer's signup picks on the wizard session. Pass any " +
      "fields you've collected: tier ('sandbox' or 'launch'), product_name, " +
      "domain, email, first_name, region, billing_cycle ('annual'/'rolling'), " +
      "github_username, description. Call this incrementally as the customer " +
      "answers questions; you don't have to set everything at once.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: SESSION_ID_SCHEMA,
        choices: {
          type: "object",
          properties: {
            tier: { type: "string", enum: ["sandbox", "launch", "growth"] },
            product_name: { type: "string", minLength: 1, maxLength: 120 },
            description: { type: "string", maxLength: 2000 },
            domain: { type: "string", maxLength: 255 },
            email: { type: "string", format: "email" },
            first_name: { type: "string", maxLength: 80 },
            region: {
              type: "string",
              description: "City code: fsn1, nbg1, hel1, ash, hil, sin.",
            },
            billing_cycle: { type: "string", enum: ["annual", "rolling"] },
            github_username: { type: "string", maxLength: 39 },
          },
          additionalProperties: false,
        },
      },
      required: ["session_id", "choices"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { session_id: string; choices: Record<string, unknown> };
      const sid = encodeURIComponent(args.session_id);
      const res = await api.patch<{
        id: string; status: string;
        choices: Record<string, unknown>;
      }>(`/api/wizard/sessions/${sid}`, args.choices);
      const summary = Object.entries(res.choices)
        .map(([k, v]) => `  ${k}: ${String(v)}`)
        .join("\n");
      return {
        text: `Session ${res.id} (${res.status}) now has:\n${summary || "  (nothing)"}\n\n` +
              `Once tier+product_name+email are set (and domain+region if tier=launch), ` +
              `call quote({session_id}) for pricing, then checkout({session_id}).`,
        data: res,
      };
    },
  };
}

// ─── 5. quote ─────────────────────────────────────────────────────────────
export function quoteTool(api: ApiClient): Tool {
  return {
    name: "quote",
    description:
      "Show the customer what they'll pay based on current set_choices. " +
      "Returns setup fee, monthly cost, any domain passthrough (premium " +
      "TLDs), and a year-1 total. Call this before checkout so the customer " +
      "can confirm.",
    inputSchema: {
      type: "object",
      properties: { session_id: SESSION_ID_SCHEMA },
      required: ["session_id"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { session_id: string };
      const sid = encodeURIComponent(args.session_id);
      const res = await api.get<{
        session_id: string;
        quote: {
          tier: string;
          setup_pence: number; setup_display: string;
          monthly_pence: number; monthly_display: string;
          monthly_billing_cycle: string;
          domain_passthrough_pence: number; domain_passthrough_display: string;
          year_one_display: string;
          breakdown: Array<{ label: string; pence: number; kind: string }>;
        };
        choices: Record<string, unknown>;
      }>(`/api/wizard/sessions/${sid}/quote`);
      const q = res.quote;
      const lines = [`Quote for session ${res.session_id} (tier: ${q.tier}):`, ``];
      if (q.tier === "sandbox") {
        lines.push("  Free. No payment step — sandbox starts immediately on checkout.");
      } else {
        for (const item of q.breakdown) {
          lines.push(`  ${item.label}: ${gbp(item.pence)}${item.kind === "recurring-monthly" ? " /mo" : ""}`);
        }
        lines.push("");
        lines.push(`  Year-1 total: ${q.year_one_display}`);
      }
      lines.push("");
      lines.push("If the customer says yes, call checkout({session_id}).");
      return { text: lines.join("\n"), data: res };
    },
  };
}

// ─── 6. checkout ──────────────────────────────────────────────────────────
export function checkoutTool(api: ApiClient): Tool {
  return {
    name: "checkout",
    description:
      "Issue a Paddle checkout link the customer pays in their browser. " +
      "For Sandbox tier (free) this returns an install_token directly and " +
      "provisioning starts immediately. For Launch/Growth tier returns a " +
      "checkout_url — show it as a clickable link, NEVER auto-follow it.",
    inputSchema: {
      type: "object",
      properties: { session_id: SESSION_ID_SCHEMA },
      required: ["session_id"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { session_id: string };
      const sid = encodeURIComponent(args.session_id);
      const res = await api.post<{
        session_id: string; tier: string;
        checkout_required: boolean;
        checkout_url: string | null;
        install_token?: string;
        status: string;
        message?: string;
      }>(`/api/wizard/sessions/${sid}/checkout`, {});

      if (!res.checkout_required) {
        return {
          text:
            `${res.message || "Provisioning started."}\n` +
            `Next: poll status({session_id}) every ~10 seconds. When it ` +
            `returns status='ready', call complete({session_id, install_token}) ` +
            `with the install_token returned here.\n\n` +
            `install_token: ${res.install_token}`,
          data: res,
        };
      }
      return {
        text:
          `Open this link in your browser to pay:\n\n  ${res.checkout_url}\n\n` +
          `After the customer pays, the page will redirect back to ` +
          `hatchik.com/wizard/return — once there, call status({session_id}) ` +
          `to watch provisioning progress.`,
        data: res,
      };
    },
  };
}

// ─── 7. status ────────────────────────────────────────────────────────────
export function statusTool(api: ApiClient): Tool {
  return {
    name: "status",
    description:
      "Poll provisioning status. Returns one of: new, in_progress, " +
      "awaiting_pay, provisioning, ready, completed, expired, cancelled. " +
      "Call this every 10-15s after checkout until 'ready', then call " +
      "complete().",
    inputSchema: {
      type: "object",
      properties: { session_id: SESSION_ID_SCHEMA },
      required: ["session_id"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { session_id: string };
      const sid = encodeURIComponent(args.session_id);
      const res = await api.get<{
        session_id: string; status: string;
        signup_status?: string;
        product_name?: string;
        domain?: string;
        install_token_available?: boolean;
      }>(`/api/wizard/sessions/${sid}/status`);
      const lines = [`Session ${res.session_id}: ${res.status}`];
      if (res.signup_status) lines.push(`  signup: ${res.signup_status}`);
      if (res.product_name) lines.push(`  product: ${res.product_name}`);
      if (res.domain) lines.push(`  domain: ${res.domain}`);
      if (res.status === "ready") {
        lines.push("");
        lines.push("✓ Provisioning complete. Call complete({session_id, install_token}) — the install_token was returned by checkout.");
      } else if (res.status === "provisioning") {
        lines.push("");
        lines.push("Still provisioning — usually takes a few mins. Re-poll in ~10s.");
      } else if (res.status === "awaiting_pay") {
        lines.push("");
        lines.push("Waiting for payment to land. Once the customer pays, this flips to 'provisioning'.");
      } else if (res.status === "expired" || res.status === "cancelled") {
        lines.push("");
        lines.push(`Session is ${res.status}. Start a new one with start_signup.`);
      }
      return { text: lines.join("\n"), data: res };
    },
  };
}

// ─── 8. complete ──────────────────────────────────────────────────────────
export function completeTool(api: ApiClient): Tool {
  return {
    name: "complete",
    description:
      "Finalise the signup. Call only after status returns 'ready'. " +
      "Trades the install_token (returned by checkout) for an api_key + " +
      "project metadata. Persist the api_key into the MCP's own config " +
      "and tell the customer their setup is live.",
    inputSchema: {
      type: "object",
      properties: {
        session_id: SESSION_ID_SCHEMA,
        install_token: {
          type: "string", minLength: 10, maxLength: 64,
          description: "The install_token returned by checkout. One-time-use.",
        },
      },
      required: ["session_id", "install_token"],
      additionalProperties: false,
    },
    async handler(input) {
      const args = input as { session_id: string; install_token: string };
      const sid = encodeURIComponent(args.session_id);
      const res = await api.post<{
        ok: boolean; session_id: string; signup_id: number;
        api_key: string; api_url: string;
        project: { id: string; product_name?: string; domain?: string; tier?: string };
      }>(`/api/wizard/sessions/${sid}/complete`, {
        install_token: args.install_token,
      });
      const p = res.project;
      const lines = [
        `✓ ${p.product_name || "Your project"} is live${p.domain ? ` at https://${p.domain}` : ""}.`,
        ``,
        `Tier:  ${p.tier ?? "sandbox"}`,
        `Project id:  ${p.id}`,
        ``,
        `To switch this MCP server into ops mode, paste these into your AI`,
        `tool's MCP config under the "hatchik" server's "env" block:`,
        ``,
        `  HATCHIK_API_KEY=${res.api_key}`,
        `  HATCHIK_API_URL=${res.api_url}`,
        ``,
        `Restart the MCP (Cursor/Claude/Windsurf does this automatically when`,
        `you edit the file). After restart, ops tools (project_info, services,`,
        `mobile_builds_list, …) will be available without leaving chat.`,
      ];
      return { text: lines.join("\n"), data: res };
    },
  };
}

// ─── Registry ─────────────────────────────────────────────────────────────
export function buildSignupTools(api: ApiClient): Tool[] {
  return [
    startSignupTool(api),
    suggestDomainsTool(api),
    checkDomainTool(api),
    setChoicesTool(api),
    quoteTool(api),
    checkoutTool(api),
    statusTool(api),
    completeTool(api),
  ];
}
