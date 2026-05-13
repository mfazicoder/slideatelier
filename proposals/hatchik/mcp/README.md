# @hatchik/mcp

MCP server for Hatchik. Lets you drive your Hatchik account from Claude,
Cursor, or Windsurf without leaving the chat — list your sandboxes,
inspect what's wired in each tenant, kick off mobile builds, and (once
the in-chat signup flow lands) sign up from your AI conversation.

## Install

In your AI tool's MCP config:

```jsonc
// .cursor/mcp.json, ~/.claude/mcp.json, or the Windsurf MCP settings panel
{
  "mcpServers": {
    "hatchik": {
      "command": "npx",
      "args": ["-y", "@hatchik/mcp"],
      "env": {
        "HATCHIK_API_KEY": "<your hatchik_session cookie value>"
      }
    }
  }
}
```

Until the dedicated API-key endpoint ships, `HATCHIK_API_KEY` is your
`hatchik_session` cookie value — copy it from `hatchik.com/account` while
signed in. Once the Bearer-auth endpoint exists server-side this same
config will accept a long-lived API key instead.

Restart your AI tool. The hatchik server should appear with a green dot.

## What it does

Mode is decided at startup by whether `HATCHIK_API_KEY` is set.

### Ops mode (key is set)

| Tool | What |
|---|---|
| `project_info` | Account email + GitHub handle + every active sandbox/launch tenant |
| `list_sandboxes` | All tenants on this account, including decommissioned, with URLs and repo links |
| `services` | "What's wired" inventory for one tenant — quotas, available-on-upgrade list |
| `mobile_builds_list` | Recent iOS + Android build runs for a tenant, with artefact download URLs once builds finish |
| `mobile_build_trigger` | Kick off a fresh iOS + Android build. Rate-limited server-side to 3/hour per tenant |

### Signup mode (no key)

| Tool | What |
|---|---|
| `start_signup` | Currently returns a pointer to `hatchik.com/start`. In-chat signup tools (`suggest_domains`, `set_choices`, `quote`, `checkout`, `complete`) are being built — see `proposals/hatchik/mcp-signup-flow.md` for the design. |

## Local development

```bash
cd proposals/hatchik/mcp
npm install
npm test                 # 26 tests across config, API client, tools
npm run build            # tsc → dist/
npm run dev              # tsx watch — auto-reload on changes
```

Point the dev MCP at a local signup-service:

```bash
HATCHIK_API_URL=http://localhost:8090 \
HATCHIK_API_KEY="<local session cookie>" \
HATCHIK_MCP_DEBUG=1 \
  node dist/index.js
```

`HATCHIK_MCP_DEBUG=1` logs every HTTP call (to stderr — the MCP stdio
stream on stdout stays clean).

## Architecture

```
src/
├── index.ts             stdio MCP server entry; wires tool registry to JSON-RPC
├── config.ts            env-var parsing, mode detection, log helper (stderr-only)
├── api.ts               fetch wrapper: Bearer + Cookie auth, error mapping
└── tools/
    ├── index.ts         tool registry — picks ops vs signup tools by mode
    ├── types.ts         Tool contract (name, description, inputSchema, handler)
    ├── project-info.ts  GET /api/account/me — account overview
    ├── list-sandboxes.ts GET /api/account/me — tenant list
    ├── services.ts      GET /api/account/services/{slug} — inventory
    ├── mobile-builds.ts GET / POST /api/account/mobile-builds/{slug}[/trigger]
    └── signup-stubs.ts  placeholder while signup-mode backend is built
```

Each tool is a self-contained module exporting a factory `fn(api): Tool`
that returns the `Tool` contract. The registry in `tools/index.ts` calls
the factories in the right mode. Adding a new tool is one file plus one
line in the registry.

## Server-side TODO

The MCP works against the existing `signup-service` endpoints today.
These backend pieces are pending and will unlock more of the surface:

- **Bearer-auth endpoint**: `POST /api/account/api-keys` to issue
  long-lived API keys tied to a session. Until then, `HATCHIK_API_KEY`
  has to be a session cookie value.
- **Wizard sessions**: `POST /api/wizard/sessions`,
  `GET /api/wizard/sessions/{id}`, `POST /api/wizard/sessions/{id}/quote`,
  `POST /api/wizard/sessions/{id}/checkout` — these power the in-chat
  signup-mode tools. Design at `proposals/hatchik/mcp-signup-flow.md`.
- **Domain availability**: `GET /api/domains/check`,
  `GET /api/domains/suggest`. Probably wraps a registrar API
  (Namecheap / Porkbun).
- **Browser-confirmation flow** for destructive ops (`apply_migration`,
  `deploy_to_prod`, `rollback`, `team_invite`, `cancel_subscription`):
  one-time tokens minted server-side, surfaced in chat as a clickable
  link, consumed on the resulting browser page.

## Tests

```bash
npm test
```

26 tests:
- **config (6)** — mode detection, URL normalisation, malformed URL rejection,
  debug flag parsing
- **api (8)** — Bearer + Cookie headers, JSON parsing, 204 / 401 / 429
  handling, path validation, network-error hinting
- **tools (12)** — every tool handler with stubbed `ApiClient`. No network.

## Licence

Business Source License 1.1 — same as the Hatchik substrate. Converts to
Apache 2.0 on the change date specified in `LICENSE`.
