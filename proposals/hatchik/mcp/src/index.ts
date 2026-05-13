#!/usr/bin/env node
/**
 * @hatchik/mcp — MCP server entry point.
 *
 * Spawned by Claude / Cursor / Windsurf via `npx -y @hatchik/mcp`. Speaks
 * the Model Context Protocol over stdio. Tools call into the Hatchik
 * signup-service HTTP API (default api.hatchik.com, override via
 * HATCHIK_API_URL).
 *
 * Mode is decided at startup based on whether HATCHIK_API_KEY is set:
 *   - unset → signup mode (one tool: start_signup, points at the web wizard)
 *   - set   → ops mode (full surface: project_info, list_sandboxes, services,
 *             mobile_builds_list, mobile_build_trigger, …)
 *
 * The MCP server registers tools dynamically; the AI client lists them via
 * the standard `tools/list` request.
 */

import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from "@modelcontextprotocol/sdk/types.js";

import { loadConfig, log } from "./config.js";
import { ApiError, makeApiClient } from "./api.js";
import { buildToolList, type Tool } from "./tools/index.js";

async function main(): Promise<void> {
  let config;
  try {
    config = loadConfig();
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    log("error", "config:", msg);
    process.exit(2);
  }

  const api = makeApiClient(config);
  const tools = buildToolList(config, api);
  const toolMap = new Map<string, Tool>();
  for (const t of tools) {
    toolMap.set(t.name, t);
  }

  log(
    "info",
    `starting in ${config.mode} mode against ${config.apiUrl} ` +
      `with ${tools.length} tool${tools.length === 1 ? "" : "s"}`,
  );

  const server = new Server(
    {
      name: "@hatchik/mcp",
      version: "0.1.0",
    },
    {
      capabilities: {
        tools: {},
      },
    },
  );

  // ─── tools/list ─────────────────────────────────────────────────────
  server.setRequestHandler(ListToolsRequestSchema, async () => {
    return {
      tools: tools.map((t) => ({
        name: t.name,
        description: t.description,
        inputSchema: t.inputSchema,
      })),
    };
  });

  // ─── tools/call ─────────────────────────────────────────────────────
  server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    const tool = toolMap.get(name);
    if (!tool) {
      return {
        content: [
          {
            type: "text",
            text: `Unknown tool: ${name}. Available: ${tools.map((t) => t.name).join(", ")}`,
          },
        ],
        isError: true,
      };
    }

    try {
      const result = await tool.handler(args ?? {});
      return {
        content: [{ type: "text", text: result.text }],
      };
    } catch (err) {
      const message =
        err instanceof ApiError
          ? err.message
          : err instanceof Error
            ? `${err.name}: ${err.message}`
            : String(err);
      log("error", `tool ${name} failed:`, message);
      return {
        content: [{ type: "text", text: message }],
        isError: true,
      };
    }
  });

  // ─── Wire stdio + run ───────────────────────────────────────────────
  const transport = new StdioServerTransport();
  await server.connect(transport);
  log("info", "stdio transport connected; awaiting requests");

  // Graceful shutdown on parent disconnect.
  const shutdown = (signal: string) => {
    log("info", `received ${signal}; closing`);
    void server.close().finally(() => process.exit(0));
  };
  process.once("SIGINT", () => shutdown("SIGINT"));
  process.once("SIGTERM", () => shutdown("SIGTERM"));
}

main().catch((err) => {
  log("error", "fatal:", err);
  process.exit(1);
});
