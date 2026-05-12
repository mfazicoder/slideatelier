# Hatchik Sandbox Orchestrator

Multi-tenant provisioning for the free Sandbox tier. One Hetzner CAX21 host
runs ~10 tenant sandboxes side-by-side, each on a unique port, with
host-level Caddy doing wildcard TLS + subdomain routing.

## Architecture

```
                          ┌─────────────────────────────────────┐
   *.hatchik.com  ──DNS── │   Hetzner CAX21 (178.105.139.144)   │
   ────────────────────── │                                     │
   wildcard A record      │   ┌──────────────────────────────┐  │
   → 178.105.139.144      │   │ Host Caddy (port 80/443)     │  │
                          │   │  wildcard TLS via CF DNS-01  │  │
                          │   │  routes by subdomain:        │  │
                          │   │   prepsheet → :18000         │  │
                          │   │   otheridea → :18001         │  │
                          │   │   ...                        │  │
                          │   └──────────────────────────────┘  │
                          │            │                         │
                          │   ┌────────┴────────┐                │
                          │   │ Tenant compose  │                │
                          │   │ stacks          │                │
                          │   │ (per-sandbox)   │                │
                          │   │ — caddy:18000   │                │
                          │   │ — postgres      │                │
                          │   │ — gotrue        │                │
                          │   │ — storage       │                │
                          │   │ — rest          │                │
                          │   │ — api (FastAPI) │                │
                          │   │ — web (Vite)    │                │
                          │   └─────────────────┘                │
                          └─────────────────────────────────────┘
```

Per-tenant footprint: ~9 containers, ~600 MB RAM idle, ~5 GB disk.
Host capacity: 10 tenants comfortable, 12 tight. Grows to schema-per-tenant
when we cross 25 sandboxes.

## Pieces

| File | What |
|---|---|
| `provision.py` | Takes a signup row from the signups DB → allocates port + slug → renders a per-tenant compose stack from the substrate-template → starts it → updates host Caddy → sends "your sandbox is ready" email |
| `decommission.py` | Inverse of provision — stops tenant containers, removes from Caddy, frees the port (used when customer upgrades to Launch or churns out) |
| `host-caddy/Caddyfile.template` | Host-level Caddy config with the per-tenant routes templated in |
| `host-caddy/docker-compose.yml` | Runs host Caddy with the cloudflare DNS plugin baked in (custom image build) |
| `host-caddy/Dockerfile.caddy-cf` | Caddy image with `caddy-dns/cloudflare` module compiled in |
| `registry.json` | Source of truth: which slugs are allocated to which ports, status, signup linkage |
| `setup-host.sh` | One-time host bootstrap — creates `/opt/hatchik-tenants/` tree, sets up the host-Caddy systemd unit, installs the registry file |

## Tenant lifecycle

1. Signup form posts `{email, idea, product_name, plan: sandbox}` to `/api/signup` on the signup service
2. signup-service inserts the row, fires welcome email ("we're setting your sandbox up — link within 5 min")
3. signup-service shells out to `provision.py <signup_id>`
4. provision.py:
   a. Slugifies the product name → `prepsheet` (with collision suffix if needed)
   b. Allocates next free port from 18000–18099 range via registry
   c. Renders `/opt/hatchik-tenants/<slug>/docker-compose.yml` from the substrate-template
   d. Renders `.env` with tenant-specific secrets (random JWT_SECRET, slugified subdomain, etc.)
   e. `docker compose up -d` in that directory
   f. Polls tenant Caddy on its allocated port until healthy
   g. Updates host Caddyfile with the new route
   h. `systemctl reload hatchik-caddy`
   i. Sends second email: "your sandbox is ready at https://<slug>.hatchik.com"
   j. Marks registry entry as `status: live`
5. Customer can hit `https://<slug>.hatchik.com`, magic-link in, and play

## What you bring to use it

- Cloudflare API token with `Zone:DNS:Edit` for `hatchik.com` (one-time, baked into host Caddy as `CF_API_TOKEN` env var for DNS-01 challenges)
- The Hetzner sandbox host SSH key (`~/.ssh/hatchik-deploy`)
- The signup service running and writing to its SQLite

## Limits + assumptions

- One sandbox host. Multi-host (Hetzner cluster) is a v2 concern.
- Sandbox slugs are immutable. Customer-chosen sandbox subdomains can't be renamed without a re-provision.
- Sandbox sandboxes share a host so noisy-neighbor is possible. Caddy has rate limits; substrate has connection pools; mostly fine for the "kick the tyres" tier.
- 100 MB disk + 3 users per tenant — enforced at the substrate level (Supabase storage caps + a sign-up gate in the API), not at the orchestrator level.
