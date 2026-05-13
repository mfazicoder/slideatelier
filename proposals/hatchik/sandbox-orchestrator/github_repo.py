"""
github_repo.py — per-tenant GitHub repo creation.

Called by provision.py once the tenant directory has been rendered and
the substrate is healthy. Pushes the rendered substrate as the initial
commit and (if the customer supplied their GitHub username) invites them
as an owner-collaborator on the repo.

The token is a classic PAT or fine-grained PAT scoped to the
``HATCHIK_GITHUB_ORG`` org with ``repo`` + ``admin:org`` (for member
invites). Not required for provisioning to succeed — when
``HATCHIK_GITHUB_TOKEN`` is empty this module logs and returns ``None``,
which leaves the AI_CONTEXT.md file in the tenant dir so the customer
can manually push it later.

No external libs beyond what provision.py already uses (httpx, stdlib).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

import httpx


GITHUB_API_URL = "https://api.github.com"
GITHUB_TOKEN = os.environ.get("HATCHIK_GITHUB_TOKEN", "")
GITHUB_ORG = os.environ.get("HATCHIK_GITHUB_ORG", "hatchik-sandboxes")
GITHUB_TIMEOUT = 15.0

# Where the redeploy webhook lives. Override via HATCHIK_PUBLIC_BASE_URL
# in tests or a staging environment; production stays on hatchik.com.
PUBLIC_BASE_URL = os.environ.get("HATCHIK_PUBLIC_BASE_URL", "https://hatchik.com").rstrip("/")


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _api_post(path: str, payload: dict[str, Any]) -> httpx.Response:
    return httpx.post(
        f"{GITHUB_API_URL}{path}",
        headers=_headers(),
        json=payload,
        timeout=GITHUB_TIMEOUT,
    )


def _api_put(path: str, payload: dict[str, Any]) -> httpx.Response:
    return httpx.put(
        f"{GITHUB_API_URL}{path}",
        headers=_headers(),
        json=payload,
        timeout=GITHUB_TIMEOUT,
    )


def _create_org_repo(slug: str, description: str) -> str | None:
    """Create a private repo under HATCHIK_GITHUB_ORG. Returns clone URL or None."""
    r = _api_post(
        f"/orgs/{GITHUB_ORG}/repos",
        {
            "name": slug,
            "description": description[:350],
            "private": True,
            "has_issues": True,
            "has_projects": False,
            "has_wiki": False,
            "auto_init": False,
        },
    )
    if r.status_code == 201:
        return r.json().get("clone_url")
    if r.status_code == 422 and "already exists" in r.text.lower():
        # Idempotent — fall back to fetching the existing repo's clone URL.
        existing = httpx.get(
            f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{slug}",
            headers=_headers(),
            timeout=GITHUB_TIMEOUT,
        )
        if existing.status_code == 200:
            return existing.json().get("clone_url")
    print(f"WARN: GitHub repo create failed ({r.status_code}): {r.text[:200]}")
    return None


def _invite_collaborator(slug: str, github_username: str) -> bool:
    """Invite the customer to the repo as an admin collaborator."""
    r = _api_put(
        f"/repos/{GITHUB_ORG}/{slug}/collaborators/{github_username}",
        {"permission": "admin"},
    )
    if r.status_code in (201, 204):
        return True
    print(f"WARN: GitHub invite for {github_username} failed ({r.status_code}): {r.text[:200]}")
    return False


def _register_redeploy_webhook(slug: str, deploy_token: str) -> bool:
    """Register a push webhook on the tenant repo pointing at the
    Hatchik redeploy endpoint.

    GitHub signs each delivery with HMAC-SHA256(deploy_token, body)
    and sends it as ``X-Hub-Signature-256``. The signup-service
    verifies the signature against the same ``deploy_token`` stored
    in the tenant's registry entry.

    Best-effort: any failure logs and returns False. The endpoint still
    works via the X-Deploy-Token header, so AI-tool-driven redeploys
    are unaffected even if webhook registration fails.

    Idempotent: GitHub does not de-dupe webhook URLs, so we first list
    existing hooks and skip if one with the same target URL already
    exists (re-running provision must not pile up duplicate hooks).
    """
    if not GITHUB_TOKEN:
        return False
    hook_url = f"{PUBLIC_BASE_URL}/api/tenants/{slug}/redeploy"
    try:
        existing = httpx.get(
            f"{GITHUB_API_URL}/repos/{GITHUB_ORG}/{slug}/hooks",
            headers=_headers(),
            timeout=GITHUB_TIMEOUT,
        )
        if existing.status_code == 200:
            for hook in existing.json():
                if (hook.get("config") or {}).get("url") == hook_url:
                    return True
    except httpx.HTTPError as e:
        print(f"WARN: failed to list existing hooks for {slug}: {e}")

    payload = {
        "name": "web",
        "active": True,
        "events": ["push"],
        "config": {
            "url": hook_url,
            "content_type": "json",
            "secret": deploy_token,
            "insecure_ssl": "0",
        },
    }
    try:
        r = _api_post(f"/repos/{GITHUB_ORG}/{slug}/hooks", payload)
    except httpx.HTTPError as e:
        print(f"WARN: redeploy webhook create raised for {slug}: {e}")
        return False
    if r.status_code in (200, 201):
        return True
    print(f"WARN: redeploy webhook create failed for {slug} ({r.status_code}): {r.text[:200]}")
    return False


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _push_initial_commit(target: Path, clone_url: str) -> bool:
    """Initialise git in the tenant dir and push the rendered substrate.

    The authenticated push URL embeds the PAT as the password — basic auth
    over HTTPS. We strip it out of the remote after pushing so the URL
    saved in .git/config can't leak the token if the tenant dir is copied.
    """
    if not GITHUB_TOKEN:
        return False
    # x-access-token is GitHub's documented username for PAT-based basic
    # auth; it works for both classic and fine-grained tokens.
    authed_url = clone_url.replace(
        "https://", f"https://x-access-token:{GITHUB_TOKEN}@", 1
    )
    try:
        if not (target / ".git").exists():
            _git(["init", "-b", "main"], target)
            # Local-only identity for the commit; doesn't leak anywhere
            # since the repo's owner gets credited via the PAT-owner
            # association on GitHub's side.
            _git(["config", "user.email", "noreply@hatchik.com"], target)
            _git(["config", "user.name", "Hatchik Bot"], target)
            # Don't commit secrets — the per-tenant .env contains the
            # Supabase service-role JWT + Postgres password.
            gitignore = target / ".gitignore"
            existing = gitignore.read_text() if gitignore.exists() else ""
            entries = ["\n# Hatchik tenant secrets\n", ".env\n", ".env.local\n"]
            if ".env" not in existing:
                gitignore.write_text(existing + "".join(entries))
            _git(["add", "-A"], target)
            _git(["commit", "-m", "Initial substrate from Hatchik"], target)
        _git(["remote", "remove", "origin"], target)  # ignore failure
    except subprocess.CalledProcessError:
        pass
    try:
        _git(["remote", "add", "origin", authed_url], target)
        _git(["push", "-u", "origin", "main"], target)
        # Replace the authed URL with the clean clone URL so .git/config
        # doesn't store the token long-term.
        _git(["remote", "set-url", "origin", clone_url], target)
        return True
    except subprocess.CalledProcessError as e:
        print(f"WARN: git push to {GITHUB_ORG}/{target.name} failed: {e.stderr.decode(errors='replace')[:300]}")
        return False


def create_tenant_repo(
    slug: str,
    target: Path,
    product_name: str,
    idea: str,
    github_username: str | None,
    deploy_token: str | None = None,
) -> dict[str, Any]:
    """Create a GitHub repo for the tenant, push the substrate, invite the user.

    ``deploy_token`` (when provided) is registered as the GitHub webhook
    secret so pushes trigger the Hatchik redeploy endpoint. The token is
    optional for backwards-compat with old call sites; without it, no
    webhook is registered and the customer can still redeploy via the
    direct X-Deploy-Token header.

    Returns ``{"repo_url": str | None, "invited": bool, "pushed": bool,
    "webhook": bool, "skipped_reason": str | None}``. Never raises —
    provision.py treats the result as informational.
    """
    if not GITHUB_TOKEN:
        return {
            "repo_url": None,
            "invited": False,
            "pushed": False,
            "webhook": False,
            "skipped_reason": "HATCHIK_GITHUB_TOKEN not set",
        }

    description = f"{product_name} — Hatchik sandbox. {idea[:200]}".strip()
    clone_url = _create_org_repo(slug, description)
    if not clone_url:
        return {
            "repo_url": None,
            "invited": False,
            "pushed": False,
            "webhook": False,
            "skipped_reason": "repo creation failed (see logs)",
        }

    # Strip ``.git`` for the human-facing URL the email + AI_CONTEXT use.
    repo_url = clone_url.removesuffix(".git")
    pushed = _push_initial_commit(target, clone_url)
    invited = False
    if github_username:
        invited = _invite_collaborator(slug, github_username)
    webhook = False
    if deploy_token:
        webhook = _register_redeploy_webhook(slug, deploy_token)

    return {
        "repo_url": repo_url,
        "invited": invited,
        "pushed": pushed,
        "webhook": webhook,
        "skipped_reason": None,
    }
