# Signup via AI MCP (LaunchKit-from-Cursor)

A second entry path into LaunchKit. The customer adds our MCP server to
their AI coder (Cursor / Claude Code / Windsurf), chats with their AI,
and the entire signup happens through the conversation — never opening
the web wizard. After provisioning, the same MCP doubles as their
ongoing ops control plane: deploy status, preview URLs, migration
approval, rollback. They never have to context-switch.

This is the differentiator that nobody else in the space can copy
quickly. ShipFast can't. Vercel can't. Lovable can't. The audience
that already lives in Cursor is exactly our target.

## Dual-entry model

```
                ┌──────────────────────────────────────────┐
                │       LaunchKit backend (single)         │
                │                                          │
                │   ┌─────────────────────────────────┐    │
                │   │   Wizard session orchestrator   │    │
                │   └─────────────────────────────────┘    │
                │              │            │              │
                │   ┌──────────┴────┐  ┌────┴───────────┐  │
                │   │ Provisioning  │  │  Billing /     │  │
                │   │ pipeline      │  │  Stripe        │  │
                │   └───────────────┘  └────────────────┘  │
                └────────▲────────────────────────▲────────┘
                         │                        │
        ┌────────────────┴───────────┐  ┌─────────┴─────────────┐
        │      LaunchKit Web         │  │   LaunchKit MCP       │
        │      (Next.js app)         │  │   (npm package)       │
        │                            │  │                       │
        │  • Hero, marketing, FAQ    │  │  • Tools (callable)   │
        │  • 4-step wizard           │  │  • Resources (read)   │
        │  • Stripe Checkout         │  │  • Prompts (slash cmd)│
        │  • Dashboard               │  │  • Confirmation flow  │
        └────────────▲───────────────┘  └──────────▲────────────┘
                     │                              │
                customer's                  customer's AI coder
                browser                     (Cursor/Claude/Windsurf)
```

Both clients are thin shells over the same backend. The wizard session
model is identical — same data, same provisioning pipeline, same
checkout — only the chrome differs. A customer can start in MCP and
finish in browser, or vice versa.

## MCP server: `@launchkit/mcp`

Published to npm. Customers install via their AI tool's MCP config:

```jsonc
// .cursor/mcp.json or ~/.claude/mcp.json
{
  "mcpServers": {
    "launchkit": {
      "command": "npx",
      "args": ["-y", "@launchkit/mcp"],
      "env": {
        // Empty before signup. After signup, contains:
        // "LAUNCHKIT_PROJECT_ID": "...",
        // "LAUNCHKIT_API_KEY": "..."
      }
    }
  }
}
```

If env vars are empty, the MCP is in "signup mode" — only signup tools
are exposed. After signup completes, the MCP writes the project ID + API
key back into its own config (with the customer's approval) and exposes
the full ops surface.

### Tools (callable by the AI)

**Signup mode:**

| Tool | Inputs | Behaviour | Confirms in browser? |
|---|---|---|---|
| `start_signup` | `{description, product_name?}` | Creates a wizard session. Returns session ID + suggested name if not provided. | No |
| `suggest_domains` | `{base_name, tlds?, count?}` | Returns N available domain candidates with prices. Uses registrar APIs. | No |
| `check_domain` | `{domain}` | Boolean + price. | No |
| `set_choices` | `{session_id, choices}` | Records the customer's picks (name, domain, region, email). | No |
| `quote` | `{session_id}` | Returns one-time + monthly totals based on current choices. | No |
| `checkout` | `{session_id}` | Returns a single-use Stripe Checkout URL bound to this session. The AI surfaces this as a clickable link. | Browser pays |
| `status` | `{session_id}` | Polls provisioning progress. Returns step list + timings. AI can call this periodically and report inline. | No |
| `complete` | `{session_id, install_token}` | Once provisioning finishes, AI calls this. MCP writes the project credentials back into its own config and switches to ops mode. | No (install_token from browser) |

**Ops mode (after signup):**

| Tool | Inputs | Behaviour | Confirms in browser? |
|---|---|---|---|
| `project_info` | — | Project metadata: domain, region, repo URL, Linear board URL. | No |
| `deploy_status` | — | Current prod + preview deploy state, recent failures. | No |
| `preview_url` | `{branch}` | URL of the named branch's preview deploy. | No |
| `pending_migrations` | — | Migrations waiting for approval. | No |
| `apply_migration` | `{migration_file}` | Applies a pending migration after browser confirmation. | **Yes** |
| `deploy_to_prod` | `{branch}` | Promotes a branch to prod after browser confirmation. | **Yes** |
| `rollback` | `{to_snapshot_id}` | Restores a nightly snapshot after browser confirmation. | **Yes** |
| `read_logs` | `{service?, since?, lines?}` | Recent logs from the named service. | No |
| `recent_errors` | `{since?}` | Sentry-style error summary. | No |
| `team_invite` | `{email, role}` | Invites a teammate to the LaunchKit project after browser confirmation. | **Yes** |
| `cancel_subscription` | — | Returns a billing-portal URL. | Browser cancels |

### Resources (readable by the AI without explicit tool calls)

| Resource URI | Content |
|---|---|
| `launchkit://project` | Project metadata (current ops mode only) |
| `launchkit://deploys/recent` | Last 20 deploys with status |
| `launchkit://migrations/pending` | Pending migration details |
| `launchkit://snapshots` | Restorable nightly snapshots, listed by date |
| `launchkit://backlog` | Proxy to Linear MCP if installed; otherwise omitted |

### Prompts (slash commands the user can type)

| Prompt | Effect |
|---|---|
| `/launchkit start` | Starts a new signup wizard in the current chat. |
| `/launchkit connect <project_id>` | Connect this MCP to an existing project (for second machine, team member). |
| `/launchkit deploy` | Walks user through deploying a feature branch to prod. |
| `/launchkit help` | Brief overview of available commands. |

## Signup sequence (MCP path)

```
User                AI            LaunchKit MCP       Backend          Stripe         Browser
 │                  │                  │                │                │              │
 │ "build me a      │                  │                │                │              │
 │  meal planner    │                  │                │                │              │
 │  called          │                  │                │                │              │
 │  MealMate"       │                  │                │                │              │
 │─────────────────▶│                  │                │                │              │
 │                  │ start_signup     │                │                │              │
 │                  │ ({description})  │                │                │              │
 │                  │─────────────────▶│ POST /sessions │                │              │
 │                  │                  │───────────────▶│                │              │
 │                  │                  │   {session_id} │                │              │
 │                  │                  │◀───────────────│                │              │
 │                  │                  │                │                │              │
 │                  │ suggest_domains  │                │                │              │
 │                  │ ({base:mealmate})│                │                │              │
 │                  │─────────────────▶│ GET /domains   │                │              │
 │                  │                  │───────────────▶│                │              │
 │                  │                  │◀───────────────│                │              │
 │ "show me .com,   │                  │                │                │              │
 │  .app, .cafe"    │                  │                │                │              │
 │◀─────────────────│                  │                │                │              │
 │─────────────────▶│                  │                │                │              │
 │                  │ set_choices      │                │                │              │
 │                  │ ({domain,region})│                │                │              │
 │                  │─────────────────▶│ PATCH /session │                │              │
 │                  │                  │───────────────▶│                │              │
 │                  │ quote            │                │                │              │
 │                  │─────────────────▶│ GET /quote     │                │              │
 │                  │                  │───────────────▶│                │              │
 │                  │                  │ {€290+€19/mo}  │                │              │
 │                  │                  │◀───────────────│                │              │
 │ "confirm? €290   │                  │                │                │              │
 │  + €19/mo"       │                  │                │                │              │
 │◀─────────────────│                  │                │                │              │
 │ "yes pay"        │                  │                │                │              │
 │─────────────────▶│                  │                │                │              │
 │                  │ checkout         │                │                │              │
 │                  │─────────────────▶│ POST /checkout │                │              │
 │                  │                  │───────────────▶│                │              │
 │                  │                  │                │  create session│              │
 │                  │                  │                │───────────────▶│              │
 │                  │                  │                │◀───────────────│              │
 │                  │                  │   {stripe_url} │                │              │
 │                  │                  │◀───────────────│                │              │
 │ "open this link  │                  │                │                │              │
 │  to pay ↗"       │                  │                │                │              │
 │◀─────────────────│                  │                │                │              │
 │                                                                                       │
 │  customer clicks link, browser opens Stripe Checkout                                  │
 │──────────────────────────────────────────────────────────────────────────────────────▶│
 │                                                                       │ pay          │
 │                                                                       │◀─────────────│
 │                                                          webhook      │              │
 │                                                  ◀────────────────────│              │
 │                                                  │   mark paid                       │
 │                                                  │   start provisioning              │
 │                                                  │   issue install_token             │
 │                                                                                       │
 │                  │                  │                │                │   "back to   │
 │                  │                  │                │                │   cursor →"  │
 │ "ok paid"        │                  │                │                │              │
 │─────────────────▶│ status           │                │                │              │
 │                  │─────────────────▶│ GET /status    │                │              │
 │                  │                  │───────────────▶│                │              │
 │                  │                  │ {provisioning} │                │              │
 │                  │                  │◀───────────────│                │              │
 │ "provisioning... │                  │                │                │              │
 │  while we wait,  │                  │                │                │              │
 │  what's the      │                  │                │                │              │
 │  first feature?" │                  │                │                │              │
 │◀─────────────────│                  │                │                │              │
 │                                                                                       │
 │       [2 minutes of feature planning while infra provisions in background]            │
 │                                                                                       │
 │                  │ status           │                │                │              │
 │                  │─────────────────▶│ GET /status    │                │              │
 │                  │                  │───────────────▶│                │              │
 │                  │                  │  {complete}    │                │              │
 │                  │                  │◀───────────────│                │              │
 │                  │ complete         │                │                │              │
 │                  │ (install_token)  │                │                │              │
 │                  │─────────────────▶│ POST /install  │                │              │
 │                  │                  │───────────────▶│                │              │
 │                  │                  │ {project_id,   │                │              │
 │                  │                  │  api_key}      │                │              │
 │                  │                  │◀───────────────│                │              │
 │                  │  writes .env +   │                │                │              │
 │                  │  updates own MCP │                │                │              │
 │                  │  config          │                │                │              │
 │                  │                  │                │                │              │
 │ "✓ your app is   │                  │                │                │              │
 │  live at         │                  │                │                │              │
 │  mealmate.cafe.  │                  │                │                │              │
 │  repo cloned.    │                  │                │                │              │
 │  start with      │                  │                │                │              │
 │  LIN-23?"        │                  │                │                │              │
 │◀─────────────────│                  │                │                │              │
```

The customer's perception: one conversation. One pay-step (in browser).
Two minutes of small talk while infra spins up. Then they're building.

## The browser-confirmation pattern

For any destructive or money-moving action, the MCP returns a payload
the AI is instructed to present as a clickable URL. The browser handles
the actual confirmation.

Example: AI calls `rollback({to_snapshot_id: "2026-05-11T03:00Z"})`. MCP
returns:

```json
{
  "status": "pending_confirmation",
  "summary": "Restore database to 2026-05-11 03:00 UTC. 4,212 records will be replaced.",
  "confirm_url": "https://launchkit.app/confirm/r/aB12cD34?ttl=300",
  "expires_in_seconds": 300
}
```

The AI is instructed (via the MCP's `description` + the LaunchKit
CLAUDE.md preamble) to present this verbatim, never to auto-follow such
links itself. Browser confirmation pages show the action plainly, the
customer clicks "Yes, restore" or "Cancel," and the backend executes.

This pattern is robust against prompt injection because:
- A malicious code comment can't generate a valid confirm token (those
  come only from the backend after a legitimate MCP call).
- Even if the AI is tricked into calling `rollback`, the browser step
  surfaces the action to the customer before execution.
- Tokens are one-time-use, short-lived, action-bound, and IP-checked.

Read-only and preview-environment actions don't go through this gate —
the friction would be unbearable for normal use.

## Discoverability — how customers find the MCP path

Three channels:

1. **Direct from launchkit.app** — the homepage hero links to
   `launchkit.app/install`, which shows the install snippet for each
   supported AI tool. This is the "two paths in" message on the
   marketing page.

2. **MCP registries** — submit to Anthropic's MCP server registry,
   Cursor's MCP marketplace, smithery.ai, and any other directories.
   Our target audience already browses these.

3. **Cross-promotion with Linear MCP** — same audience overlap. Linear
   maintains a "complementary tools" list; we get listed there.

For v1, web is the primary path. MCP path is the "power user" path that
sells itself through differentiation and word-of-mouth in dev Twitter /
Reddit /r/cursor etc.

## Onboarding wrinkle: empty MCP config

When a brand-new customer installs `@launchkit/mcp` and has no project
yet, the MCP exposes only the signup tools. First time they invoke
anything, the AI sees descriptions like:

> `start_signup`: Begin LaunchKit signup. If you have no LAUNCHKIT_PROJECT_ID set, this is your entry point.

The AI naturally guides toward signup. No manual onboarding screen
needed.

## After signup: the MCP rewrites its own config

This is the cleanest implementation but worth being explicit. When the
backend confirms provisioning is complete, the MCP:

1. Receives the `install_token` from the browser via deep link
   (`cursor://launchkit/install?token=...` or via local OAuth-style
   redirect to `http://localhost:55555/install`)
2. Calls `complete(session_id, install_token)` against the backend
3. Receives `{project_id, api_key}`
4. Writes them to the customer's `.cursor/mcp.json` (or equivalent)
   under the existing `launchkit` server's `env` block
5. Reloads — AI tools poll MCP config and pick up new env vars on
   next request

Cursor and Claude Code both support env hot-reload via SIGHUP-style
mechanisms. Worst case: customer restarts their AI client, takes 3
seconds.

## What we do NOT trust the MCP to do

| Action | Why not |
|---|---|
| Take credit cards | PCI scope + customer trust — always Stripe in browser |
| Cancel subscriptions | Real revenue risk + dispute risk — browser via Stripe portal |
| Delete projects | Destructive + irreversible — browser confirmation |
| Show secrets in chat | Token leakage risk — secrets viewable only in browser dashboard |
| Approve team members | Auth boundary — browser only |
| Edit billing details | PCI + tax compliance — Stripe portal |

These rules are encoded in the MCP itself (returns confirm URLs) AND in
the backend (rejects requests without confirm tokens). Defence in depth.

## Trust signals

The MCP needs to land with customers as "safe to install" on day one.
Tactics:

- **Open-source** the MCP under MIT. Customers can audit before install.
- **Pinned versions** in the install snippet (`@launchkit/mcp@1.2.3`)
  with a clear changelog. Auto-updating MCPs erode trust.
- **Permission scopes printed at install** ("This MCP can: create
  domains, deploy to your servers, request payment via browser. It
  cannot: take credit cards in chat, deploy without browser
  confirmation, cancel your subscription.").
- **Audit log** in the dashboard showing every MCP-initiated action.
- **Approved by** badges from Cursor / Anthropic / Linear MCP
  marketplaces, when we can earn them.

## Build estimate (additive on top of the v1 spec)

| Component | Effort |
|---|---|
| MCP server scaffold (Node + `@modelcontextprotocol/sdk`) | 1 day |
| Signup-mode tools + backend session API | 2 days |
| Ops-mode tools + backend ops API | 2 days |
| Browser confirmation token system | 1 day |
| Install-token handoff (browser → MCP) | 1 day |
| Audit log + dashboard surface | 1 day |
| Install snippets for Cursor/Claude/Windsurf, plus docs page | 1 day |
| Open-source release + npm publish + registry submissions | 0.5 day |
| **Total** | **~9.5 days** |

This is additive to the existing 6-week MVP. Brings total v1 build to
roughly 7.5 weeks. Worth it — this is the differentiator.

## Edge cases

| Case | Handling |
|---|---|
| Customer never pays | Session expires after 24h, MCP reports "expired" on next status check. |
| Customer pays but never returns to chat | Provisioning still completes. We email the install snippet. Customer can install via web. |
| Customer's AI is offline mid-provisioning | Provisioning is server-side and unaffected. Customer can reconnect later via `/launchkit connect <id>` once they have the project ID from email. |
| Customer wants to abandon mid-flow | `cancel_signup` tool, refunds N/A pre-payment. Post-payment: 14-day refund policy via browser. |
| Customer has multiple LaunchKit projects | `connect_project` switches active project. Could also support multi-project via env-vars-per-MCP-server-instance. |
| Customer is on a flaky connection | All MCP calls are idempotent (session_id keyed). Retry-safe. |
| AI hallucinates choices | Two-layer defence: (1) MCP returns the canonical state in every response so the AI re-grounds; (2) destructive actions need browser. |
| Customer's AI is on an org that disallows external MCPs | They fall back to web path. The marketing page should make both paths equally welcoming. |
| Tool descriptions get out of date with backend | Schema versioning. MCP includes a `min_backend_version` and warns the customer when out of date. |

## What we don't build in v1

- Voice signup via MCP (would be amazing, complex)
- Team-mode MCP (multiple developers sharing a single project's MCP)
- Bring-your-own-LLM MCP (a LaunchKit-flavoured MCP that doesn't go
  through the standard AI clients — i.e. a hosted chat at
  launchkit.app/chat). Tempting, but it dilutes positioning. Stick
  with "use your AI tool."
- Plug-ins for non-MCP AI tools (Copilot, JetBrains AI). Wait for
  signal.

## Why this wins

Three reasons this entry path is uniquely defensible:

1. **It's where the audience already lives.** A Cursor power user doesn't
   want a web wizard, they want chat. Meeting them in their tool is
   higher conversion than landing-page-and-pricing-table.
2. **The same surface serves signup AND ops.** That's not a feature —
   that's the product. Stripe doesn't have it. Vercel doesn't have it.
   ShipFast can't add it.
3. **It's a forcing function for trust.** Building the browser-
   confirmation pattern correctly is hard, but once done it's a moat —
   nobody wants to redo it, and customers won't tolerate a competitor
   who skipped it.

The MCP path turns LaunchKit from "a productized substrate" into "an
ambient companion to your AI coder." That's the version of the pitch
that earns a $19/mo line item that nobody cancels.
