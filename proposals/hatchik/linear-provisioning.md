# Linear Provisioning Flow

How LaunchKit goes from "customer described their product" to "Linear board
seeded, MCP wired, AI coder reading the backlog" — without ever asking the
customer to copy-paste an API key or pick a project ID.

## What we can and can't automate

| Step | Automatable? |
|---|---|
| Create a Linear **workspace** | ❌ — workspace creation is an interactive signup. Linear has no API for it. |
| Authorize LaunchKit against an **existing workspace** | ✅ — OAuth 2.0 |
| Pick or create a **team** in that workspace | ✅ — `teamCreate` mutation |
| Create a **project** | ✅ — `projectCreate` mutation |
| Create **labels**, **cycles**, **issues** in bulk | ✅ — batched GraphQL mutations |
| Wire **MCP config** into the customer's repo | ✅ — file write at provisioning time |
| Mirror **issue status** as PRs move | ✅ — Linear's own GitHub integration handles this |

The unautomatable step (workspace signup) is real, but it's a 60-second
interaction. We minimize the friction by deep-linking with a return URL.

## Sequence

```
Customer            LaunchKit Wizard            Linear              LLM
  │                       │                       │                  │
  │ 1. Submit wizard      │                       │                  │
  │──────────────────────▶│                       │                  │
  │                       │ 2. Generate backlog   │                  │
  │                       │──────────────────────────────────────────▶
  │                       │                       │                  │
  │ 3. "Connect Linear"   │                       │                  │
  │    button shown       │                       │                  │
  │◀──────────────────────│                       │                  │
  │                       │                       │                  │
  │ 4. Click → OAuth      │                       │                  │
  │──────────────────────▶│ 5. Redirect to        │                  │
  │                       │    linear.app/oauth   │                  │
  │                       │──────────────────────▶│                  │
  │                                               │                  │
  │ 6. Approve in Linear  │                       │                  │
  │──────────────────────────────────────────────▶│                  │
  │                                               │                  │
  │                       │ 7. Callback w/ code   │                  │
  │                       │◀──────────────────────│                  │
  │                       │ 8. Exchange for token │                  │
  │                       │──────────────────────▶│                  │
  │                       │◀──────────────────────│                  │
  │                       │ 9. Fetch workspaces   │                  │
  │                       │──────────────────────▶│                  │
  │                       │◀──────────────────────│                  │
  │ 10. Pick workspace    │                       │                  │
  │    (if multiple)      │                       │                  │
  │◀──────────────────────│                       │                  │
  │──────────────────────▶│                       │                  │
  │                       │ 11. Create team       │                  │
  │                       │    + project          │                  │
  │                       │    + labels           │                  │
  │                       │    + cycles           │                  │
  │                       │──────────────────────▶│                  │
  │                       │ 12. Bulk create       │                  │
  │                       │    20 issues          │                  │
  │                       │──────────────────────▶│                  │
  │                       │ 13. Mark substrate    │                  │
  │                       │    issues completed   │                  │
  │                       │──────────────────────▶│                  │
  │                       │ 14. Register webhook  │                  │
  │                       │──────────────────────▶│                  │
  │                       │                       │                  │
  │                       │ 15. Write MCP config  │                  │
  │                       │    into customer repo │                  │
  │                       │                       │                  │
  │ 16. "Your board is    │                       │                  │
  │    ready" + link      │                       │                  │
  │◀──────────────────────│                       │                  │
```

Total customer-facing time: ~30 seconds for OAuth, ~10 seconds for the
provisioning to finish. The backlog generation (~6 seconds with Sonnet)
runs in parallel with the OAuth flow, so it's ready by the time the
issues need to be created.

## OAuth configuration

**LaunchKit's Linear OAuth app:**
- Registered at `linear.app/settings/api/applications`
- Public app, so any Linear workspace can install it
- Redirect URI: `https://launchkit.app/auth/linear/callback`
- Scopes requested:
  - `read` — fetch workspaces, teams, existing data
  - `write` — create teams, projects, issues, labels, webhooks
  - `admin` — needed for `webhookCreate` on team scope

**Authorization URL:**
```
https://linear.app/oauth/authorize
  ?client_id={LAUNCHKIT_CLIENT_ID}
  &redirect_uri=https://launchkit.app/auth/linear/callback
  &response_type=code
  &scope=read,write,admin
  &state={signed_session_token}
  &prompt=consent
```

**Token exchange (callback):**
```http
POST https://api.linear.app/oauth/token
Content-Type: application/x-www-form-urlencoded

client_id={LAUNCHKIT_CLIENT_ID}
&client_secret={LAUNCHKIT_CLIENT_SECRET}
&redirect_uri=https://launchkit.app/auth/linear/callback
&code={callback_code}
&grant_type=authorization_code
```

Returns: `access_token`, `expires_in` (typically 10 years for Linear),
`scope`. No refresh token — Linear tokens are long-lived.

We store the access token encrypted (KMS-wrapped) in our DB under the
customer record. It's used for ongoing webhook health checks and any
post-launch automation. The customer can revoke it from Linear at any
time without breaking their LaunchKit deployment (we only need the token
for setup and ongoing convenience features).

## Key GraphQL mutations

All against `https://api.linear.app/graphql` with
`Authorization: Bearer {access_token}`.

### Fetch workspaces (after OAuth)
```graphql
query {
  viewer {
    id
    name
    organization {
      id
      name
      urlKey
    }
  }
}
```
A single token is bound to a single organization, so this returns one
workspace. (If a user has multiple workspaces, they OAuth once per workspace.)

### Create team
```graphql
mutation TeamCreate($input: TeamCreateInput!) {
  teamCreate(input: $input) {
    success
    team { id key name }
  }
}
```
Variables:
```json
{
  "input": {
    "name": "MealMate",
    "key": "LIN",
    "description": "MealMate development",
    "icon": "🍳",
    "color": "#6366F1",
    "cycleEnabled": true,
    "cycleStartDay": 1,
    "cycleDuration": 2
  }
}
```

If a team already exists with a similar name, we skip and use it.

### Create project
```graphql
mutation ProjectCreate($input: ProjectCreateInput!) {
  projectCreate(input: $input) {
    success
    project { id name url }
  }
}
```
Variables:
```json
{
  "input": {
    "name": "MealMate v1",
    "description": "Built with LaunchKit · meal planner for couples",
    "teamIds": ["{team_id}"],
    "targetDate": "2026-08-01"
  }
}
```

### Create labels (batched)
For each of the 12 allowed labels:
```graphql
mutation LabelCreate($input: IssueLabelCreateInput!) {
  issueLabelCreate(input: $input) {
    success
    issueLabel { id name }
  }
}
```

### Bulk create issues
Linear doesn't have a single bulk endpoint, so we batch ~5 mutations per
HTTP request using GraphQL aliases:
```graphql
mutation BulkCreate(
  $i0: IssueCreateInput!, $i1: IssueCreateInput!,
  $i2: IssueCreateInput!, $i3: IssueCreateInput!,
  $i4: IssueCreateInput!
) {
  c0: issueCreate(input: $i0) { success issue { id identifier } }
  c1: issueCreate(input: $i1) { success issue { id identifier } }
  c2: issueCreate(input: $i2) { success issue { id identifier } }
  c3: issueCreate(input: $i3) { success issue { id identifier } }
  c4: issueCreate(input: $i4) { success issue { id identifier } }
}
```

Each `IssueCreateInput` looks like:
```json
{
  "teamId": "{team_id}",
  "projectId": "{project_id}",
  "title": "Couples can create a household and invite a partner",
  "description": "...markdown body...\n\n## Acceptance criteria\n- ...",
  "priority": 1,
  "estimate": 5,
  "labelIds": ["{frontend_id}", "{backend_id}", "{auth_id}"],
  "stateId": "{todo_state_id}"
}
```

For Substrate-epic issues (`pre_completed: true`), `stateId` is set to
the team's `Done` state instead.

20 issues take 4 batched requests (~1s total wall time at Linear's
typical p50 response time).

### Webhook registration
```graphql
mutation WebhookCreate($input: WebhookCreateInput!) {
  webhookCreate(input: $input) {
    success
    webhook { id url }
  }
}
```
```json
{
  "input": {
    "url": "https://launchkit.app/webhooks/linear/{customer_id}",
    "teamId": "{team_id}",
    "resourceTypes": ["Issue", "Comment"],
    "label": "LaunchKit sync"
  }
}
```

The webhook lets us mirror status changes back to a customer's dashboard
("3 stories shipped this week"), and is useful for future features
(slack notifications, weekly summaries).

## Files written into the customer's repo

After provisioning, the worker writes/updates the following:

### `.cursor/mcp.json`
```json
{
  "mcpServers": {
    "linear": {
      "command": "npx",
      "args": ["-y", "@linear/mcp-server"],
      "env": {
        "LINEAR_API_KEY": "{personal_or_oauth_token}",
        "LINEAR_TEAM_ID": "{team_id}",
        "LINEAR_PROJECT_ID": "{project_id}"
      }
    }
  }
}
```

### `mcp.json` (for Claude Code)
Same structure, under repo root. Claude Code respects both `mcp.json` and
`.mcp.json`.

### `CLAUDE.md` — additions
```markdown
## Backlog

This project's backlog lives in Linear:
- Team: LIN (MealMate)
- Project: MealMate v1 — {linear_project_url}

You can read and write the backlog via the Linear MCP server (already
configured in `mcp.json`). When the user asks "what's next?", query the
top-priority unblocked issues and surface them. When you finish a
feature, comment on the relevant issue with the PR link — Linear's
GitHub integration will auto-move the issue to "In Review".

Branch naming convention: `feat/{issue_identifier}-{kebab-name}`,
e.g. `feat/LIN-23-recipes-crud`. Include the issue identifier in commit
messages so Linear's GitHub integration can auto-link.
```

### `.env` (managed by LaunchKit, never committed)
```
LINEAR_API_KEY={oauth_token}
LINEAR_TEAM_ID={team_id}
LINEAR_PROJECT_ID={project_id}
```

The OAuth token is server-side only; it's not exposed to client-side
code. AI tools running locally use their MCP config which reads the
same token from the env file LaunchKit syncs to the developer's machine
on first `git clone` (via a `setup.sh` script).

## Edge cases

| Case | Handling |
|---|---|
| Customer declines OAuth | Wizard offers "Skip — set up later." Customer can connect Linear from the LaunchKit dashboard at any time; provisioning runs deferred. |
| Customer has multiple Linear workspaces | Each Linear OAuth token is bound to one workspace, so the OAuth flow itself surfaces the picker. If we want LaunchKit to operate across multiple, the customer re-runs OAuth per workspace. |
| Customer doesn't have Linear at all | "Connect Linear" button deep-links to `linear.app/signup?from=launchkit` with a `state` param so we return them to the wizard. 60-second detour. |
| Customer already has a Linear project for this product | OAuth scope `read` checks for an existing project with a matching name. If found, we offer "Use the existing project" instead of creating a new one — and seed the backlog into it (with a clear "Generated by LaunchKit" label). |
| Linear API rate limited | Linear's GraphQL rate limit is generous (300 req/min for OAuth apps). We batch and back off; if we hit 429, retry with exponential backoff up to 30s. Provisioning is non-time-critical so this is acceptable. |
| LLM backlog generation fails | Fall back to a generic SaaS backlog (Substrate + 10 generic Core stories like "Build the main feature", "Add settings page", etc.). Email customer apologizing and offering a manual regen later. |
| Customer revokes the OAuth token later | Their stack keeps working (Linear is independent of the running app). The "what's next?" MCP integration stops working until they re-authorize. We email them. |
| Workspace owner removes LaunchKit user from workspace | Same as above — no app downtime, just no more Linear sync. |
| Customer wants to use Jira / GitHub Projects / Notion instead | v1: not supported. v2: same provisioning shape, different MCP server. Architecture supports it — the orchestrator just calls a different `BacklogProvider` implementation. |

## Status sync — Linear ↔ GitHub

Linear has a first-class GitHub integration that does most of what we
need without any custom code. When the customer's repo is created:

1. We install the **Linear app for GitHub** on the customer's repo
   (via Linear's API: `integrationGithubConnect` mutation).
2. Customer's CLAUDE.md teaches the AI to:
   - Branch from `main` as `feat/LIN-23-{slug}`
   - Reference the issue ID in commit messages
   - Open PRs with `Fixes LIN-23` in the body
3. Linear's GitHub app then auto-moves issues:
   - PR opened → "In Review"
   - PR merged → "Done"
   - PR closed without merge → "Cancelled"

This means LaunchKit doesn't need to write any sync code — we just wire
Linear and GitHub together once at provisioning time, and the rest is
free.

## What LaunchKit stores

```sql
CREATE TABLE linear_integrations (
  customer_id           UUID PRIMARY KEY REFERENCES customers(id),
  linear_workspace_id   TEXT NOT NULL,
  linear_workspace_name TEXT NOT NULL,
  linear_team_id        TEXT NOT NULL,
  linear_team_key       TEXT NOT NULL,
  linear_project_id     TEXT NOT NULL,
  linear_project_url    TEXT NOT NULL,
  access_token_encrypted BYTEA NOT NULL,
  webhook_id            TEXT,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  revoked_at            TIMESTAMPTZ
);
```

The encrypted token is wrapped with a KMS key. We never log the
plaintext.

## Build estimate

| Component | Effort |
|---|---|
| Linear OAuth app registration + callback handling | 0.5 day |
| GraphQL client wrapper (Python or TS) | 0.5 day |
| Team/project/label/cycle creation flow | 0.5 day |
| Bulk issue creation with batching + retry | 1 day |
| Backlog generation prompt + JSON validation | 1 day |
| MCP config file generation + repo wiring | 0.5 day |
| GitHub ↔ Linear integration wiring | 0.5 day |
| Customer dashboard view ("Your Linear board: {url}") | 0.5 day |
| Webhook receiver (basic) | 0.5 day |
| Error handling + observability + retries | 1 day |
| **Total** | **~6 days** |

Fits comfortably inside the 6-week MVP build window.

## What we don't build in v1

- Customer-facing Linear UI inside LaunchKit (use Linear.app directly).
- Two-way sync of comments (Linear's GitHub app handles status, which is
  what matters).
- Bulk story regeneration from the dashboard ("Add 5 more stories about
  X") — v2 feature.
- Cross-workspace federation (one customer, multiple Linear workspaces).
- Non-Linear backlog providers (Jira, GitHub Projects, Notion) — v2/v3.
