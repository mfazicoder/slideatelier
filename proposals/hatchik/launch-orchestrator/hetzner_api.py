"""
Thin wrapper over the Hetzner Cloud API.

Documents what Launch tier needs and nothing more. The full SDK
(`hcloud` on PyPI) is fine but we keep this as a stdlib-only wrapper so
the orchestrator host doesn't need anything beyond `httpx`.

Reference: https://docs.hetzner.cloud/

Required env:
    HETZNER_API_TOKEN  — read+write project-scoped token from Hetzner Cloud
                         console -> Security -> API Tokens

Optional env:
    HETZNER_SSH_KEY_NAME  — name of an SSH key already uploaded to Hetzner
                            Cloud (defaults to "hatchik-orchestrator").
                            promote.py uses this key to SSH into the new
                            server to run the substrate deploy.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

API_BASE = "https://api.hetzner.cloud/v1"
DEFAULT_TIMEOUT = 30.0


class HetznerError(RuntimeError):
    pass


def _client() -> httpx.Client:
    token = os.environ.get("HETZNER_API_TOKEN")
    if not token:
        raise HetznerError(
            "HETZNER_API_TOKEN not set. Generate one in Hetzner Cloud Console "
            "-> Security -> API Tokens (read+write)."
        )
    return httpx.Client(
        base_url=API_BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=DEFAULT_TIMEOUT,
    )


def _raise_for(resp: httpx.Response) -> None:
    if 200 <= resp.status_code < 300:
        return
    body = resp.text[:1500]
    raise HetznerError(f"Hetzner API {resp.request.method} {resp.url.path} "
                       f"-> {resp.status_code}: {body}")


# ─── Servers ────────────────────────────────────────────────────────────

# Launch tier default. CAX31: 4 vCPU ARM, 8 GB RAM, 80 GB disk, ~€7.20/mo.
# Plenty of headroom for substrate Postgres + Supabase + customer app.
# Growth tier upgrades in place to CAX41 (8 vCPU, 16 GB) by hcloud action.
LAUNCH_SERVER_TYPE = "cax31"
GROWTH_SERVER_TYPE = "cax41"
DEFAULT_IMAGE = "debian-12"

# Region mapping — Hetzner location codes. Customer's chosen region (free
# field on signup) is mapped to the closest Hetzner location.
REGION_TO_LOCATION = {
    "eu-central":  "nbg1",  # Nuremberg, DE
    "eu-west":     "fsn1",  # Falkenstein, DE
    "eu-helsinki": "hel1",  # Helsinki, FI
    "us-east":     "ash",   # Ashburn, VA
    "us-west":     "hil",   # Hillsboro, OR
    "ap-singapore": "sin",  # Singapore (CAX class not yet available in SIN
                            # at time of writing — falls back to FSN)
}


def map_region(customer_region: str | None) -> str:
    """Map a customer-facing region string to a Hetzner location code.

    Falls back to nbg1 if unknown. Logged at orchestrator level so we
    notice and add mappings as customers request unusual regions.
    """
    if not customer_region:
        return "nbg1"
    return REGION_TO_LOCATION.get(customer_region.lower(), "nbg1")


def list_servers() -> list[dict[str, Any]]:
    """Return all servers in the project."""
    with _client() as c:
        r = c.get("/servers", params={"per_page": 50})
        _raise_for(r)
        return r.json()["servers"]


def get_server(server_id: int) -> dict[str, Any]:
    with _client() as c:
        r = c.get(f"/servers/{server_id}")
        _raise_for(r)
        return r.json()["server"]


def list_ssh_keys() -> list[dict[str, Any]]:
    with _client() as c:
        r = c.get("/ssh_keys")
        _raise_for(r)
        return r.json()["ssh_keys"]


def get_ssh_key_id_by_name(name: str) -> int | None:
    for k in list_ssh_keys():
        if k.get("name") == name:
            return k["id"]
    return None


def create_server(
    *,
    name: str,
    location: str,
    server_type: str = LAUNCH_SERVER_TYPE,
    image: str = DEFAULT_IMAGE,
    ssh_key_name: str | None = None,
    user_data: str | None = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a server. Returns the API's full response (server + action).

    ``user_data`` is a cloud-init script — first-boot bootstrap to install
    Docker, set up swap, lock down SSH, etc. promote.py supplies one.

    ``labels`` are key-value tags Hetzner uses for filtering. We tag each
    Launch server with hatchik_tenant_slug, hatchik_signup_id, hatchik_tier.
    """
    if ssh_key_name is None:
        ssh_key_name = os.environ.get("HETZNER_SSH_KEY_NAME", "hatchik-orchestrator")
    key_id = get_ssh_key_id_by_name(ssh_key_name)
    if key_id is None:
        raise HetznerError(
            f"SSH key '{ssh_key_name}' not found in Hetzner Cloud. Upload it "
            "to Cloud Console -> Security -> SSH Keys before provisioning."
        )

    payload: dict[str, Any] = {
        "name": name,
        "server_type": server_type,
        "image": image,
        "location": location,
        "ssh_keys": [key_id],
        "start_after_create": True,
    }
    if user_data:
        payload["user_data"] = user_data
    if labels:
        payload["labels"] = labels

    with _client() as c:
        r = c.post("/servers", json=payload)
        _raise_for(r)
        return r.json()


def delete_server(server_id: int) -> None:
    with _client() as c:
        r = c.delete(f"/servers/{server_id}")
        _raise_for(r)


def snapshot_server(server_id: int, description: str) -> dict[str, Any]:
    """Take a snapshot (full disk image) of the server.

    Used before decommission for the 30-day "you can still come back"
    retention window. Snapshots cost €0.0119/GB/month — cheap insurance.
    """
    with _client() as c:
        r = c.post(
            f"/servers/{server_id}/actions/create_image",
            json={"type": "snapshot", "description": description},
        )
        _raise_for(r)
        return r.json()


def change_type(server_id: int, server_type: str) -> dict[str, Any]:
    """Resize the server (used for Launch -> Growth upgrade).

    Requires the server to be off first. Returns the action; caller polls
    /actions/<id> to wait for completion.
    """
    with _client() as c:
        # Power off
        c.post(f"/servers/{server_id}/actions/poweroff")
        # Wait for off (poll)
        for _ in range(30):
            time.sleep(2)
            srv = get_server(server_id)
            if srv["status"] == "off":
                break
        else:
            raise HetznerError(f"Server {server_id} did not power off in time")
        # Resize
        r = c.post(
            f"/servers/{server_id}/actions/change_type",
            json={"server_type": server_type, "upgrade_disk": True},
        )
        _raise_for(r)
        action = r.json()
        # Power back on
        c.post(f"/servers/{server_id}/actions/poweron")
        return action


def wait_for_running(server_id: int, timeout_s: int = 300) -> dict[str, Any]:
    """Poll until the server is ``running`` or timeout. Returns final state."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        srv = get_server(server_id)
        if srv["status"] == "running":
            return srv
        time.sleep(5)
    raise HetznerError(f"Server {server_id} did not reach 'running' within {timeout_s}s")
