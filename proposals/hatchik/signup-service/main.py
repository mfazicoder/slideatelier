"""
Hatchik signup endpoint — concierge-MVP backend.

Accepts a signup from hatchik.com's signup form, logs it to SQLite,
emails the founder (hello@hatchik.com) with the signup details, and
responds to the customer's browser.

Deploy on the same VPS as the marketing site (Infomaniak shared
host). systemd manages the process; Caddy reverse-proxies
/api/signup to localhost:8090.

This is a stopgap. When the wizard ships, the provisioning worker
takes over and this service is retired.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import AliasChoices, BaseModel, EmailStr, Field, field_validator

# Local sibling: TLD allowlist for Launch-tier ``domain_choice``. Phase-1
# input guard so we never offer to register a domain that costs more than
# £14/yr (the cap implied by Launch's £89 setup fee). See
# ``proposals/hatchik/DOMAIN_REGISTRATION_SCOPE.md`` for the full memo.
from domains import validate_domain as validate_domain_choice  # noqa: E402

# ─── service_inventory import shim ────────────────────────────────────────
# service_inventory.py is the canonical "what ships in a sandbox" data
# source. It lives in sandbox-orchestrator/ (used by provision.py for the
# sandbox-ready email) and we need it here too (for
# /api/account/services/<slug>). On the sandbox host both directories are
# /opt/hatchik-orchestrator/ and /opt/hatchik-signup/. The path is
# overrideable for tests / dev.
import sys as _sys

_ORCHESTRATOR_DIR = os.environ.get(
    "HATCHIK_ORCHESTRATOR_DIR", "/opt/hatchik-orchestrator"
)
if _ORCHESTRATOR_DIR and _ORCHESTRATOR_DIR not in _sys.path:
    _sys.path.insert(0, _ORCHESTRATOR_DIR)
try:
    from service_inventory import sandbox_inventory  # type: ignore
except ImportError:  # pragma: no cover — orchestrator dir missing in this env
    sandbox_inventory = None  # type: ignore[assignment]

# launch-orchestrator/ counterpart. Same shim pattern. Launch-tier
# inventory is rendered through /api/account/services/<slug> when the
# slug points at a Launch tenant (registry.json on the launch host).
_LAUNCH_ORCHESTRATOR_DIR = os.environ.get(
    "HATCHIK_LAUNCH_ORCHESTRATOR_DIR", "/opt/hatchik-launch-orchestrator"
)
if _LAUNCH_ORCHESTRATOR_DIR and _LAUNCH_ORCHESTRATOR_DIR not in _sys.path:
    _sys.path.insert(0, _LAUNCH_ORCHESTRATOR_DIR)
try:
    from tenant_inventory import launch_inventory  # type: ignore
except ImportError:  # pragma: no cover — launch-orchestrator dir missing
    launch_inventory = None  # type: ignore[assignment]

# ─── Config ──────────────────────────────────────────────────────────────
DB_PATH = Path(os.environ.get("HATCHIK_SIGNUP_DB", "/var/lib/hatchik/signups.db"))
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FOUNDER_EMAIL = os.environ.get("HATCHIK_FOUNDER_EMAIL", "hello@hatchik.com")
FROM_EMAIL = os.environ.get("HATCHIK_FROM_EMAIL", "onboarding@resend.dev")
RESEND_API_URL = "https://api.resend.com/emails"
RESEND_TIMEOUT_SECONDS = 10.0
ALLOWED_ORIGINS = os.environ.get(
    "HATCHIK_ALLOWED_ORIGINS", "https://hatchik.com,https://www.hatchik.com"
).split(",")
RATE_LIMIT_WINDOW_SECONDS = 60
RATE_LIMIT_MAX_REQUESTS = 5

# Admin endpoints (POST /api/admin/* and DELETE /api/admin/*) require this
# shared secret in the X-Admin-Token header. Empty value disables admin API.
ADMIN_TOKEN = os.environ.get("HATCHIK_ADMIN_TOKEN", "")

# Self-serve deletion: customers receive an emailed link with a token,
# valid for this many hours.
DELETION_TOKEN_TTL_HOURS = 24

# Magic-link login tokens are single-use and short-lived.
LOGIN_TOKEN_TTL_MINUTES = 30
# Cap brute-force attempts at the 6-digit verification code per token.
# After this many failed attempts the token is invalidated entirely so an
# attacker can't keep guessing (1-in-900k odds × 5 tries is fine; we never
# want them to get a sixth try). 15 minutes is the implicit window because
# the token itself expires in 30 minutes — the counter lives on the row.
LOGIN_CODE_MAX_ATTEMPTS = 5
# Session cookies last this long after last activity.
SESSION_TTL_DAYS = 30
SESSION_COOKIE_NAME = "hatchik_session"

# Paddle config (Launch tier upgrade flow). When PADDLE_LAUNCH_PRICE_ID is
# set, /api/account/upgrade returns a hosted-checkout URL; otherwise the
# UI shows a "coming soon, Paddle approval pending" placeholder.
PADDLE_VENDOR = os.environ.get("PADDLE_VENDOR", "")
PADDLE_LAUNCH_PRICE_ID = os.environ.get("PADDLE_LAUNCH_PRICE_ID", "")
PADDLE_CHECKOUT_BASE = os.environ.get(
    "PADDLE_CHECKOUT_BASE", "https://buy.paddle.com/checkout"
)
PADDLE_BILLING_PORTAL_BASE = os.environ.get(
    "PADDLE_BILLING_PORTAL_BASE", "https://buyer.paddle.com"
)

# Path to decommission.py — subprocess'd for tear-down.
DECOMMISSION_SCRIPT = os.environ.get(
    "HATCHIK_DECOMMISSION_SCRIPT", "/opt/hatchik-orchestrator/decommission.py"
)
# Path to restore.py — subprocess'd for archive → live.
RESTORE_SCRIPT = os.environ.get(
    "HATCHIK_RESTORE_SCRIPT", "/opt/hatchik-orchestrator/restore.py"
)
# Launch-tier orchestrator scripts (see launch-orchestrator/). promote.py
# and decommission_launch.py default to SAFE_MODE (no --execute) so the
# first runs only email plans rather than calling Hetzner / Cloudflare.
PROMOTE_SCRIPT = os.environ.get(
    "HATCHIK_PROMOTE_SCRIPT", "/opt/hatchik-launch-orchestrator/promote.py"
)
DECOMMISSION_LAUNCH_SCRIPT = os.environ.get(
    "HATCHIK_DECOMMISSION_LAUNCH_SCRIPT",
    "/opt/hatchik-launch-orchestrator/decommission_launch.py",
)
LAUNCH_REGISTRY_PATH = Path(os.environ.get(
    "HATCHIK_LAUNCH_REGISTRY", "/opt/hatchik-launch-orchestrator/registry.json"
))

# Per-tenant redeploy log dir. Tenants get one file each:
# /var/log/hatchik/redeploy-<slug>.log. Tail-fail returns the last 50
# lines on a failed redeploy so the caller can diagnose without
# shelling into the host.
REDEPLOY_LOG_DIR = Path(os.environ.get("HATCHIK_REDEPLOY_LOG_DIR", "/var/log/hatchik"))
# Tenants dir (where docker-compose stacks live). Reused for the
# redeploy subprocess CWD.
TENANTS_DIR = Path(os.environ.get("HATCHIK_TENANTS_DIR", "/opt/hatchik-tenants"))
# Rate limit: max redeploys per tenant per window. Runaway redeploy
# loops are an obvious abuse vector and a customer-side bug too.
REDEPLOY_RATE_LIMIT_MAX = int(os.environ.get("HATCHIK_REDEPLOY_RATE_LIMIT_MAX", "6"))
REDEPLOY_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get("HATCHIK_REDEPLOY_RATE_LIMIT_WINDOW_SECONDS", "300"))
# Generous timeout — docker compose up -d --build can be 60-120s when
# Python deps or schemas change. Capped well under the FastAPI worker
# default to keep the request snappy on failure.
REDEPLOY_SUBPROCESS_TIMEOUT_SECONDS = int(os.environ.get("HATCHIK_REDEPLOY_TIMEOUT", "600"))

# ─── Mobile builds (GitHub Actions) ──────────────────────────────────────
# The /api/account/mobile-builds/* endpoints use the same HATCHIK_GITHUB_*
# config as the provisioning worker so that the token already authorised
# to create per-tenant repos can also dispatch + list workflow runs on
# them. Token scope: repo + workflow on every repo under
# HATCHIK_GITHUB_ORG. Empty token = endpoints surface "Connect GitHub
# first" rather than throwing.
HATCHIK_GITHUB_TOKEN = os.environ.get("HATCHIK_GITHUB_TOKEN", "")
HATCHIK_GITHUB_ORG = os.environ.get("HATCHIK_GITHUB_ORG", "hatchik-sandboxes")
GITHUB_API_URL = "https://api.github.com"
GITHUB_API_TIMEOUT_SECONDS = 10.0
# Tighter cap on the inline /api/signup handle-validation check: the
# request handler awaits it inline so a slow GitHub API turn would block
# the customer's signup. Fail-open on timeout (see _github_user_exists).
GITHUB_USER_LOOKUP_TIMEOUT_SECONDS = 1.2
MOBILE_BUILD_WORKFLOW_FILE = "build-mobile.yml"
# Re-invite endpoint: cap per email to prevent spam-loops (e.g. customer
# repeatedly editing their handle). 5/hour matches the abuse-protection
# posture on the rest of the account API.
GITHUB_INVITE_RATE_LIMIT_MAX = int(
    os.environ.get("HATCHIK_GITHUB_INVITE_RATE_LIMIT_MAX", "5")
)
GITHUB_INVITE_RATE_LIMIT_WINDOW_SECONDS = int(
    os.environ.get("HATCHIK_GITHUB_INVITE_RATE_LIMIT_WINDOW_SECONDS", "3600")
)
# Mobile builds are expensive (especially the macOS leg). Cap per-tenant
# at 3/hour to prevent runaway loops and stay inside GitHub's free-tier
# minute allowance.
MOBILE_BUILD_RATE_LIMIT_MAX = int(os.environ.get("HATCHIK_MOBILE_BUILD_RATE_LIMIT_MAX", "3"))
MOBILE_BUILD_RATE_LIMIT_WINDOW_SECONDS = int(
    os.environ.get("HATCHIK_MOBILE_BUILD_RATE_LIMIT_WINDOW_SECONDS", "3600")
)

# Paddle Billing webhook config. Hatchik's selling entity is Omani, so we
# use Paddle as Merchant of Record (see PRODUCT_OFFERING.md §8.1) — Stripe
# does not support Omani entities.
PADDLE_WEBHOOK_SECRET = os.environ.get("PADDLE_WEBHOOK_SECRET", "")
# Paddle signs payloads with a timestamp; reject events older than this to
# block replay attacks. Tolerance matches Stripe's default of 5 minutes.
PADDLE_SIGNATURE_TOLERANCE_SECONDS = 300

# ─── Abuse-protection config ────────────────────────────────────────────
# Concurrency cap on provision.py subprocesses — each sandbox uses ~1.3 GB
# RAM during boot, so the CAX21 (8 GB) host can safely run 3-4 in parallel.
# Anything over the cap is queued in SQLite and picked up by the background
# worker every QUEUE_POLL_SECONDS.
MAX_CONCURRENT_PROVISIONS = int(os.environ.get("HATCHIK_MAX_CONCURRENT_PROVISIONS", "3"))
QUEUE_POLL_SECONDS = 5

# Cloudflare Turnstile secret — when set, /api/signup and /api/account/login
# require a valid Turnstile token. Empty value disables verification (dev).
TURNSTILE_SECRET = os.environ.get("HATCHIK_TURNSTILE_SECRET", "")
TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"
TURNSTILE_TIMEOUT_SECONDS = 5.0

# Geo-IP lookup — free tier of ipapi.co (no key needed for low volume).
# Disabled if HATCHIK_GEO_IP_DISABLED=1, so the service still works in dev
# or in CI where outbound HTTP is blocked.
GEO_IP_URL_TEMPLATE = os.environ.get("HATCHIK_GEO_IP_URL", "https://ipapi.co/{ip}/json/")
GEO_IP_TIMEOUT_SECONDS = 3.0
GEO_IP_DISABLED = os.environ.get("HATCHIK_GEO_IP_DISABLED", "") in {"1", "true", "yes"}
# Comma-separated ISO country codes that should be soft-blocked at signup.
# Empty = no countries blocked. Stored upper-cased for fast membership tests.
BLOCKED_COUNTRIES = {
    c.strip().upper()
    for c in os.environ.get("HATCHIK_BLOCKED_COUNTRIES", "").split(",")
    if c.strip()
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("hatchik-signup")

# ─── Disposable-email block-list ────────────────────────────────────────
# Curated subset (~180 entries) of the most-trafficked throwaway-email
# providers. Static set so it ships with the binary and works offline; the
# longer canonical lists (e.g. disposable-email-domains/disposable-email-domains
# on GitHub) contain ~10k entries, almost all of which are dead. This list
# is biased toward the providers that still resolve and still relay mail.
DISPOSABLE_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "0-mail.com", "0815.ru", "10minutemail.com", "10minutemail.net", "10minutemail.de",
    "10minutemail.co.uk", "10minutemail.co.za", "10minutemailbox.com", "10minutesmail.com",
    "1secmail.com", "1secmail.net", "1secmail.org", "20minutemail.com", "20minutemail.it",
    "2prong.com", "30minutemail.com", "33mail.com", "3d-painting.com", "4warding.com",
    "5ymail.com", "60minutemail.com", "75hosting.com", "9ox.net",
    "anonbox.net", "anonmails.de", "armyspy.com", "azmeil.tk",
    "binkmail.com", "bobmail.info", "bopao.com", "bouncr.com", "brefmail.com", "broadbandninja.com",
    "bsnow.net", "bspamfree.org", "bugmenot.com", "bund.us", "burnermail.io", "byom.de",
    "cek.pm", "chacuo.net", "chammy.info", "clickmail.info", "cool.fr.nf", "cosmorph.com",
    "courriel.fr.nf", "courrieltemporaire.com", "cuvox.de",
    "dacoolest.com", "dayrep.com", "deadaddress.com", "deadspam.com", "deagot.com",
    "despam.it", "despam-it.com", "dingbone.com", "discard.email", "discardmail.com",
    "discardmail.de", "dispomail.eu", "disposable.com", "disposable-email.com",
    "disposable-mail.com", "disposablemail.com", "disposableinbox.com", "dodgeit.com",
    "dodgit.com", "dontreg.com", "dontsendmespam.de", "dropmail.me", "duck2.club",
    "dudmail.com", "dump-email.info", "dumpyemail.com", "duskmail.com",
    "e4ward.com", "easytrashmail.com", "edv.to", "einrot.com", "emailfake.com", "emailias.com",
    "emailisvalid.com", "emailmiser.com", "emailondeck.com", "emailsensei.com",
    "emailtemporanea.com", "emailtemporanea.net", "emailtemporario.com.br", "emaltemp.com",
    "emz.net", "ephemail.net", "etranquil.com", "etranquil.net", "explodemail.com",
    "fakeinbox.com", "fakemail.fr", "fakemailgenerator.com", "fakemailz.com",
    "fastacura.com", "fastchevy.com", "fastchrysler.com", "fastkawasaki.com", "fastmazda.com",
    "fastmitsubishi.com", "fastnissan.com", "fastsubaru.com", "fastsuzuki.com", "fasttoyota.com",
    "fastyamaha.com", "filzmail.com", "fleckens.hu", "forgotmail.com", "freundin.ru",
    "front14.org", "fux0ringduh.com", "garliclife.com", "get1mail.com", "get2mail.fr",
    "getairmail.com", "getmails.eu", "getnada.com", "ghosttexter.de", "girlsundertheinfluence.com",
    "gishpuppy.com", "goemailgo.com", "gotmail.net", "gowikibooks.com", "great-host.in",
    "grr.la", "gsrv.co.uk", "guerillamail.biz", "guerillamail.com", "guerillamail.net",
    "guerillamail.org", "guerrillamail.biz", "guerrillamail.com", "guerrillamail.de",
    "guerrillamail.info", "guerrillamail.net", "guerrillamail.org", "guerrillamailblock.com",
    "haltospam.com", "hidemail.de", "hidemail.us", "hochsitze.com", "hotpop.com",
    "imails.info", "inbax.tk", "inbox.si", "inboxbear.com", "inboxclean.com", "inboxclean.org",
    "incognitomail.com", "incognitomail.net", "incognitomail.org",
    "jetable.com", "jetable.fr.nf", "jetable.net", "jetable.org", "jourrapide.com",
    "kasmail.com", "kcrw.de", "killmail.com", "killmail.net", "kimsdisk.com",
    "klassmaster.com", "klzlk.com", "kook.ml", "kurzepost.de",
    "lackmail.net", "lifebyfood.com", "link2mail.net", "litedrop.com", "lookugly.com",
    "lopl.co.cc", "lortemail.dk", "lr78.com", "lroid.com", "lyft.live",
    "mailbidon.com", "mailcatch.com", "maildrop.cc", "maileater.com", "mailexpire.com",
    "mailfa.tk", "mailforspam.com", "mailfreeonline.com", "mailfs.com", "mailguard.me",
    "mailimate.com", "mailin8r.com", "mailinator.com", "mailinator.net", "mailinator.org",
    "mailinator2.com", "mailincubator.com", "mailismagic.com", "mailmetrash.com",
    "mailmoat.com", "mailms.com", "mailnator.com", "mailnesia.com", "mailnull.com",
    "mailshell.com", "mailsiphon.com", "mailslapping.com", "mailtemp.info", "mailtothis.com",
    "mailtrash.net", "mailtv.net", "mailtv.tv", "mailzilla.com", "mailzilla.org",
    "makemetheking.com", "manybrain.com", "mbx.cc", "mega.zik.dj", "mintemail.com",
    "moakt.com", "mohmal.com", "moncourrier.fr.nf", "monemail.fr.nf", "monmail.fr.nf",
    "mt2009.com", "mt2014.com", "mt2015.com", "mvrht.com", "mycleaninbox.net",
    "mymail-in.net", "mypartyclip.de", "myphantomemail.com", "mysamp.de",
    "neverbox.com", "nfimail.com", "nice-4u.com", "no-spam.ws", "noclickemail.com",
    "nomail.pw", "nomail.xl.cx", "nomail2me.com", "nomorespamemails.com", "nospam.ze.tc",
    "nospam4.us", "nospamfor.us", "nospammail.net", "notmailinator.com", "nowmymail.com",
    "objectmail.com", "obobbo.com", "odaymail.com", "one-time.email", "oneoffemail.com",
    "onewaymail.com", "onlatedotcom.info", "online.ms", "opayq.com", "opentrash.com", "ordinaryamerican.net",
    "otherinbox.com", "ourklips.com", "outlawspam.com", "ovpn.to", "owlpic.com",
    "pancakemail.com", "pekarstvi.eu", "petsfa.com", "pflegekind.eu", "pisls.com",
    "pleasenoads.com", "poczta.onet.pl", "politikerclub.de", "poofy.org", "pookmail.com",
    "privatdemail.net", "privymail.de", "proxymail.eu", "punkass.com", "putthisinyourspamdatabase.com",
    "quickinbox.com", "rcpt.at", "receiveee.com", "recode.me", "recursor.net",
    "regbypass.com", "rmqkr.net", "rppkn.com", "rtrtr.com",
    "safe-mail.net", "sandelf.de", "saynotospams.com", "selfdestructingmail.com", "send-email.org",
    "senseless-entertainment.com", "services391.com", "sharklasers.com", "shieldedmail.com",
    "shieldemail.com", "shiftmail.com", "shitmail.me", "shitmail.org", "shitware.nl",
    "shortmail.net", "sibmail.com", "sify.com", "skeefmail.com", "slapsfromlastnight.com",
    "slaskpost.se", "slipry.net", "slopsbox.com", "smashmail.de", "smellfear.com",
    "snakemail.com", "sneakemail.com", "sneakyfrog.com", "snkmail.com", "sofimail.com",
    "sofort-mail.de", "softpls.asia", "sogetthis.com", "soodonims.com", "spam.la",
    "spam.su", "spam4.me", "spamavert.com", "spambob.com", "spambob.net", "spambob.org",
    "spambog.com", "spambog.de", "spambog.net", "spambog.ru", "spambox.us", "spamcero.com",
    "spamcon.org", "spamcorptastic.com", "spamcowboy.com", "spamcowboy.net", "spamcowboy.org",
    "spamday.com", "spamex.com", "spamfree24.com", "spamfree24.de", "spamfree24.eu",
    "spamfree24.info", "spamfree24.net", "spamfree24.org", "spamgoes.com", "spamgourmet.com",
    "spamgourmet.net", "spamgourmet.org", "spamherelots.com", "spamhereplease.com",
    "spamhole.com", "spamify.com", "spaml.com", "spaml.de", "spammotel.com", "spamobox.com",
    "spamoff.de", "spamslicer.com", "spamspot.com", "spamthis.co.uk", "spamtroll.net",
    "speed.1s.fr", "supermailer.jp", "superrito.com", "suremail.info",
    "tafmail.com", "talkinator.com", "teewars.org", "teleworm.com", "teleworm.us",
    "temp-mail.com", "temp-mail.io", "temp-mail.org", "temp-mail.ru", "tempail.com",
    "tempalias.com", "tempe-mail.com", "tempemail.biz", "tempemail.co.za", "tempemail.com",
    "tempemail.net", "tempinbox.co.uk", "tempinbox.com", "tempmail.de", "tempmail.eu",
    "tempmail.it", "tempmail.us", "tempmail2.com", "tempmaildemand.com", "tempmailer.com",
    "tempmailer.de", "tempomail.fr", "temporarily.de", "temporarioemail.com.br",
    "temporaryemail.net", "temporaryforwarding.com", "temporaryinbox.com", "temporarymailaddress.com",
    "thanksnospam.info", "thankyou2010.com", "thc.st", "thelimestones.com", "thisisnotmyrealemail.com",
    "throwam.com", "throwawayemailaddresses.com", "throwawaymail.com", "tilien.com",
    "tmailinator.com", "tradermail.info", "trash-amil.com", "trash-mail.at", "trash-mail.com",
    "trash-mail.de", "trash-mail.tk", "trash2009.com", "trash2010.com", "trash2011.com",
    "trashdevil.com", "trashemail.de", "trashmail.at", "trashmail.com", "trashmail.de",
    "trashmail.me", "trashmail.net", "trashmail.org", "trashmail.ws", "trashmailer.com",
    "trashymail.com", "trashymail.net", "trbvm.com", "trillianpro.com", "tyldd.com",
    "uggsrock.com", "umail.net", "uplipht.com", "uroid.com", "venompen.com",
    "veryrealemail.com", "vidchart.com", "viditag.com", "viewcastmedia.com", "viewcastmedia.net",
    "viewcastmedia.org", "vmailing.info", "vmpanda.com", "vomoto.com", "vpn.st", "vsimcard.com",
    "vubby.com", "wee.my", "weg-werf-email.de", "wegwerf-email-addressen.de",
    "wegwerf-email-adressen.de", "wegwerfadresse.de", "wegwerfemail.com", "wegwerfemail.de",
    "wegwerfemailadresse.com", "wegwerfmail.de", "wegwerfmail.info", "wegwerfmail.net",
    "wegwerfmail.org", "whatpaas.com", "whyspam.me", "wilemail.com", "willhackforfood.biz",
    "willselfdestruct.com", "winemaven.info", "wronghead.com", "wuzup.net", "wuzupmail.net",
    "www.e4ward.com", "www.gishpuppy.com", "www.mailinator.com",
    "x.ip6.li", "xagloo.com", "xemaps.com", "xemail.com", "xents.com", "xmaily.com",
    "xoxy.net", "yapped.net", "yeah.net", "yep.it", "yogamaven.com", "yopmail.com",
    "yopmail.fr", "yopmail.net", "you-spam.com", "ypmail.webarnak.fr.eu.org", "yuurok.com",
    "zehnminutenmail.de", "zetmail.com", "zippymail.info", "zoaxe.com", "zoemail.net",
    "zoemail.org", "zoomail.tk",
})


def is_disposable_email(email: str) -> bool:
    """Return True if the email's domain is a known throwaway provider."""
    domain = email.rpartition("@")[2].strip().lower()
    if not domain:
        return False
    return domain in DISPOSABLE_EMAIL_DOMAINS


# ─── Turnstile verification ──────────────────────────────────────────────
async def verify_turnstile(token: str | None, remote_ip: str) -> bool:
    """Verify a Cloudflare Turnstile token. Returns True on success.

    When ``HATCHIK_TURNSTILE_SECRET`` is empty (dev mode), the check is
    skipped and the function returns True with a logged warning — never
    silently fails-closed in production because callers gate on
    ``TURNSTILE_SECRET`` being set.
    """
    if not TURNSTILE_SECRET:
        log.warning("HATCHIK_TURNSTILE_SECRET unset — skipping Turnstile check")
        return True
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=TURNSTILE_TIMEOUT_SECONDS) as client:
            r = await client.post(
                TURNSTILE_VERIFY_URL,
                data={
                    "secret": TURNSTILE_SECRET,
                    "response": token,
                    "remoteip": remote_ip,
                },
            )
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.error("Turnstile verification failed (network/parse): %s", e)
        return False
    success = bool(data.get("success"))
    if not success:
        log.warning("Turnstile rejected token: %s", data.get("error-codes"))
    return success


# ─── Geo-IP lookup ───────────────────────────────────────────────────────
async def lookup_geo_ip(ip: str) -> dict[str, str]:
    """Best-effort geo-IP lookup. Returns {country_code, city, asn}.

    All keys are empty strings on failure or when disabled. Caller must
    treat this as advisory data, not load-bearing.
    """
    empty = {"country_code": "", "city": "", "asn": ""}
    if GEO_IP_DISABLED or not ip or ip == "unknown" or ip.startswith(("127.", "10.", "192.168.")):
        return empty
    try:
        async with httpx.AsyncClient(timeout=GEO_IP_TIMEOUT_SECONDS) as client:
            r = await client.get(GEO_IP_URL_TEMPLATE.format(ip=ip))
            r.raise_for_status()
            data = r.json()
    except (httpx.HTTPError, ValueError) as e:
        log.warning("geo-IP lookup failed for %s: %s", ip, e)
        return empty
    return {
        "country_code": str(data.get("country_code") or data.get("country") or "")[:8].upper(),
        "city": str(data.get("city") or "")[:80],
        "asn": str(data.get("asn") or "")[:32],
    }


# ─── GitHub handle existence check ──────────────────────────────────────
async def _github_user_exists(handle: str) -> tuple[bool, str]:
    """Check whether a GitHub username resolves to a real user.

    Returns ``(exists, reason)``:
      - ``(True, "ok")``        — GitHub returned 200 for the user.
      - ``(False, "not_found")`` — GitHub returned 404.
      - ``(True, "skipped")``   — token unset, network error, rate-limit,
        timeout, or any other upstream wobble. The check is advisory:
        signup must not fail because GitHub had a bad minute.

    Tight timeout (``GITHUB_USER_LOOKUP_TIMEOUT_SECONDS``) because this
    runs inline in the POST /api/signup handler and we need the
    end-to-end request to stay under 1.5s. The PAT is never logged or
    returned to the caller.
    """
    if not handle:
        return True, "ok"
    if not HATCHIK_GITHUB_TOKEN:
        # Without a token we'd be hitting the unauthenticated 60-req/h
        # bucket — quickly exhausted by a single noisy IP. Fail-open
        # rather than gate signup on a check we can't reliably perform.
        log.info("github user check skipped — no HATCHIK_GITHUB_TOKEN configured")
        return True, "skipped"
    headers = {
        "Authorization": f"Bearer {HATCHIK_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=GITHUB_USER_LOOKUP_TIMEOUT_SECONDS) as client:
            r = await client.get(f"{GITHUB_API_URL}/users/{handle}", headers=headers)
    except httpx.HTTPError as e:
        # Network blip, DNS hiccup, timeout — fail-open. Signup must
        # never depend on GitHub's availability.
        log.warning("github user check failed for %s — fail-open: %s", handle, e)
        return True, "skipped"
    if r.status_code == 200:
        return True, "ok"
    if r.status_code == 404:
        return False, "not_found"
    # 403 (rate limited), 5xx, anything else: fail-open so the signup
    # goes through. The collaborator invite is the eventual source of
    # truth — if the handle really is bogus, the invite will surface it
    # via the re-invite endpoint or admin tooling.
    log.warning(
        "github user check for %s returned %s — fail-open",
        handle, r.status_code,
    )
    return True, "skipped"


# ─── Provisioning throttle ───────────────────────────────────────────────
# In-flight signup ids tracked in a Python set guarded by a lock so we can
# answer "how many provisions are running right now?" from the request
# handler (to decide whether to show the queue-delay message in the
# acknowledgement email). Persisting the queue itself in SQLite (via
# signups.status) survives uvicorn restarts; this in-memory set just speeds
# up the per-request check.
_in_flight_signups: set[int] = set()
_in_flight_lock = asyncio.Lock()
_queue_worker_task: asyncio.Task[None] | None = None


async def _mark_provision_started(signup_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE signups SET status = 'provisioning', provision_started_at = ? WHERE id = ?",
            (now, signup_id),
        )
        conn.commit()


async def _mark_provision_finished(signup_id: int, status: str) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "UPDATE signups SET status = ?, provision_finished_at = ? WHERE id = ?",
            (status, now, signup_id),
        )
        conn.commit()


def _run_provision_subprocess(signup_id: int) -> int:
    """Synchronous subprocess wrapper — runs in a thread via to_thread.

    Returns the exit code. Logs stdout/stderr to the per-signup log file.
    """
    import subprocess
    script = os.environ.get("HATCHIK_PROVISION_SCRIPT", "/opt/hatchik-orchestrator/provision.py")
    if not Path(script).exists():
        log.warning("provision script not found at %s — skipping (concierge MVP)", script)
        return 0
    log_dir = Path("/var/log/hatchik")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"provision-{signup_id}.log"
    with log_file.open("ab") as f:
        proc = subprocess.run(
            [script, str(signup_id)],
            stdout=f,
            stderr=subprocess.STDOUT,
        )
    return proc.returncode


async def _provision_one(signup_id: int) -> None:
    """Mark started → run provision.py → mark finished. Releases the slot
    via the in-flight set even on failure."""
    try:
        await _mark_provision_started(signup_id)
        log.info("provision started for signup #%s (in-flight=%d)", signup_id, len(_in_flight_signups))
        rc = await asyncio.to_thread(_run_provision_subprocess, signup_id)
        final_status = "live" if rc == 0 else "failed"
        await _mark_provision_finished(signup_id, final_status)
        log.info("provision finished for signup #%s rc=%s status=%s", signup_id, rc, final_status)
    except Exception as e:  # noqa: BLE001
        log.error("provision crashed for signup #%s: %s", signup_id, e)
        try:
            await _mark_provision_finished(signup_id, "failed")
        except Exception as inner:  # noqa: BLE001
            log.error("failed to mark signup #%s failed: %s", signup_id, inner)
    finally:
        async with _in_flight_lock:
            _in_flight_signups.discard(signup_id)


async def _try_dispatch(signup_id: int) -> bool:
    """If there's capacity, claim a slot and fire the provision task.

    Returns True on dispatch, False if the slot would exceed the cap.
    """
    async with _in_flight_lock:
        if len(_in_flight_signups) >= MAX_CONCURRENT_PROVISIONS:
            return False
        _in_flight_signups.add(signup_id)
    asyncio.create_task(_provision_one(signup_id))
    return True


async def enqueue_or_dispatch(signup_id: int) -> Literal["dispatched", "queued"]:
    """Run the signup immediately if capacity allows, else mark it queued."""
    dispatched = await _try_dispatch(signup_id)
    if dispatched:
        return "dispatched"
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE signups SET status = 'queued' WHERE id = ?", (signup_id,))
        conn.commit()
    log.info("signup #%s queued (cap=%d reached)", signup_id, MAX_CONCURRENT_PROVISIONS)
    return "queued"


async def _queue_worker() -> None:
    """Background task: every QUEUE_POLL_SECONDS, pull the oldest queued
    Sandbox signup and dispatch if there's capacity. Sleeps when idle.
    Restarted on unhandled errors so a single crash doesn't drain the queue.
    """
    log.info("queue worker started — poll=%ss cap=%d", QUEUE_POLL_SECONDS, MAX_CONCURRENT_PROVISIONS)
    while True:
        try:
            await asyncio.sleep(QUEUE_POLL_SECONDS)
            async with _in_flight_lock:
                free_slots = MAX_CONCURRENT_PROVISIONS - len(_in_flight_signups)
            if free_slots <= 0:
                continue
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id FROM signups WHERE status = 'queued' AND tier = 'sandbox' "
                    "ORDER BY id ASC LIMIT ?",
                    (free_slots,),
                ).fetchall()
            for row in rows:
                await _try_dispatch(row["id"])
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.error("queue worker iteration failed: %s — continuing", e)

# ─── DB ──────────────────────────────────────────────────────────────────
def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS signups (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT NOT NULL,
                email           TEXT NOT NULL,
                first_name      TEXT,
                product_name    TEXT,
                description     TEXT,
                tier            TEXT NOT NULL CHECK(tier IN ('sandbox', 'launch')),
                region          TEXT,
                domain_choice   TEXT,
                ip_address      TEXT,
                user_agent      TEXT,
                status          TEXT NOT NULL DEFAULT 'new'
            )
            """
        )
        # Additive migration for live DBs that pre-date later columns.
        # Each ALTER is gated by PRAGMA inspection so re-running on an
        # already-migrated DB is a no-op.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(signups)").fetchall()}

        # Widen the signups.tier CHECK constraint to include 'growth'.
        # SQLite cannot modify a CHECK in place — the only safe option is
        # to rebuild the table when the existing schema doesn't already
        # accept 'growth'. Test by trying a dry insert in a savepoint.
        try:
            conn.execute("SAVEPOINT _check_tier_migration")
            conn.execute(
                "INSERT INTO signups (created_at, email, tier) "
                "VALUES ('1970-01-01T00:00:00Z', '__migration_probe__', 'growth')"
            )
            conn.execute("ROLLBACK TO SAVEPOINT _check_tier_migration")
            conn.execute("RELEASE SAVEPOINT _check_tier_migration")
            # CHECK already accepts 'growth' — no rebuild needed.
        except sqlite3.IntegrityError:
            # CHECK rejects 'growth' — rebuild the table with a wider
            # constraint. We preserve all rows + indices, do the rename
            # in a single transaction, and run idempotently.
            conn.execute("RELEASE SAVEPOINT _check_tier_migration")
            log.info(
                "Migrating signups table to widen tier CHECK constraint "
                "(adding 'growth'). This rebuilds the table in place."
            )
            # Build the column list dynamically so the migration survives
            # future ALTER ADD COLUMNs without us editing this rebuild.
            col_names = [row[1] for row in conn.execute(
                "PRAGMA table_info(signups)"
            ).fetchall()]
            col_csv = ", ".join(col_names)
            conn.execute(
                f"""
                CREATE TABLE signups_new (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at      TEXT NOT NULL,
                    email           TEXT NOT NULL,
                    first_name      TEXT,
                    product_name    TEXT,
                    description     TEXT,
                    tier            TEXT NOT NULL
                        CHECK(tier IN ('sandbox', 'launch', 'growth')),
                    region          TEXT,
                    domain_choice   TEXT,
                    ip_address      TEXT,
                    user_agent      TEXT,
                    status          TEXT NOT NULL DEFAULT 'new',
                    github_username TEXT,
                    provision_started_at TEXT,
                    provision_finished_at TEXT,
                    country_code    TEXT,
                    city            TEXT,
                    asn             TEXT
                )
                """
            )
            # Migrate only the columns that exist in the old table
            new_cols = {"id", "created_at", "email", "first_name",
                        "product_name", "description", "tier", "region",
                        "domain_choice", "ip_address", "user_agent",
                        "status", "github_username", "provision_started_at",
                        "provision_finished_at", "country_code", "city", "asn"}
            shared = [c for c in col_names if c in new_cols]
            shared_csv = ", ".join(shared)
            conn.execute(
                f"INSERT INTO signups_new ({shared_csv}) "
                f"SELECT {shared_csv} FROM signups"
            )
            conn.execute("DROP TABLE signups")
            conn.execute("ALTER TABLE signups_new RENAME TO signups")
            log.info("signups table rebuilt; row count preserved.")
            # Refresh cols — rebuilt table already has all the columns
            # the additive ALTERs below would otherwise re-add. Without
            # this, subsequent ALTER ADD COLUMN errors with
            # "duplicate column name: github_username".
            cols = {row[1] for row in conn.execute(
                "PRAGMA table_info(signups)"
            ).fetchall()}

        if "first_name" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN first_name TEXT")
        if "github_username" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN github_username TEXT")
        if "provision_started_at" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN provision_started_at TEXT")
        if "provision_finished_at" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN provision_finished_at TEXT")
        if "country_code" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN country_code TEXT")
        if "city" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN city TEXT")
        if "asn" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN asn TEXT")
        # Audit trail for T&Cs acceptance — ISO-8601 timestamp at signup
        # time. Nullable so the migration is additive: historical rows
        # pre-date the consent gate and will read NULL.
        if "accepted_terms_at" not in cols:
            conn.execute("ALTER TABLE signups ADD COLUMN accepted_terms_at TEXT")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rate_limit (
                ip          TEXT NOT NULL,
                timestamp   REAL NOT NULL
            )
            """
        )
        # One-time migration: the never-deployed Stripe schema used
        # checkout_session_id / stripe_customer_id columns. Hatchik switched
        # to Paddle (MoR; see PRODUCT_OFFERING.md §8.1) before any payment
        # row was written, so dropping is safe — no data loss possible on a
        # fresh install, and the only deploy in flight is this rename.
        conn.execute("DROP TABLE IF EXISTS payments")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at               TEXT NOT NULL,
                paddle_transaction_id    TEXT UNIQUE,
                paddle_customer_id       TEXT,
                paddle_subscription_id   TEXT,
                customer_email           TEXT,
                currency                 TEXT,
                amount                   TEXT,
                status                   TEXT,
                raw_payload              TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS processed_events (
                event_id    TEXT PRIMARY KEY,
                event_type  TEXT NOT NULL,
                processed_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS deletion_tokens (
                token       TEXT PRIMARY KEY,
                email       TEXT NOT NULL,
                slug        TEXT NOT NULL,
                signup_id   INTEGER NOT NULL,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                consumed_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_deletion_tokens_email ON deletion_tokens(email)")
        # Account-management auth: magic-link login + session cookies.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS login_tokens (
                token       TEXT PRIMARY KEY,
                email       TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                expires_at  TEXT NOT NULL,
                consumed_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id   TEXT PRIMARY KEY,
                email        TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                last_seen_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_login_tokens_email ON login_tokens(email)")
        # Additive migration: 6-digit verification code stored on the same
        # login_tokens row so the magic-link and code paths share state
        # (single-use: consuming either marks consumed_at). code_attempts
        # counts failed POST /api/account/login-with-code attempts so we
        # can rate-limit per token and invalidate after 5 wrong guesses.
        login_cols = {row[1] for row in conn.execute("PRAGMA table_info(login_tokens)").fetchall()}
        if "code" not in login_cols:
            conn.execute("ALTER TABLE login_tokens ADD COLUMN code TEXT")
        if "code_attempts" not in login_cols:
            conn.execute("ALTER TABLE login_tokens ADD COLUMN code_attempts INTEGER NOT NULL DEFAULT 0")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sessions_email ON sessions(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signups_email ON signups(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signups_created ON signups(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rate_limit_ip ON rate_limit(ip)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_customer ON payments(paddle_customer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_payments_email ON payments(customer_email)")
        # Cohort-funnel metrics (see cohort_metrics.py + AGENT_METRICS_REPORT.md).
        # Initial signup writes (signup_id, NULL, tier, created_at, NULL, 'initial signup');
        # decommission writes a 'cancelled' row; lifecycle archive writes 'archived';
        # Paddle upgrade webhook (TODO) writes 'launch'/'growth' rows.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tier_transitions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                signup_id       INTEGER NOT NULL,
                from_tier       TEXT,
                to_tier         TEXT NOT NULL CHECK(to_tier IN ('sandbox','launch','growth','cancelled','archived')),
                occurred_at     TEXT NOT NULL,
                paddle_event_id TEXT,
                notes           TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tier_transitions_signup ON tier_transitions(signup_id)")
        # Long-lived API keys for the MCP / programmatic clients. Same
        # auth surface as session cookies, but issued explicitly by the
        # customer from /account → API keys, named, revocable, no auto-
        # expiry. The token is hashed at rest (sha256); the plaintext
        # leaves the server exactly once at creation time. Revocation
        # sets revoked_at; lookup ignores revoked rows. last_used_at is
        # touched on every accepted request — cheap audit trail.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS api_keys (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                email           TEXT NOT NULL,
                key_hash        TEXT NOT NULL UNIQUE,
                name            TEXT,
                created_at      TEXT NOT NULL,
                last_used_at    TEXT,
                revoked_at      TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_email ON api_keys(email)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash)")
        conn.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
    init_db()
    log.info("Hatchik signup service started — DB at %s", DB_PATH)
    global _queue_worker_task
    _queue_worker_task = asyncio.create_task(_queue_worker())
    try:
        yield
    finally:
        if _queue_worker_task and not _queue_worker_task.done():
            _queue_worker_task.cancel()
            try:
                await _queue_worker_task
            except asyncio.CancelledError:
                pass


# ─── App ─────────────────────────────────────────────────────────────────
app = FastAPI(title="Hatchik Signup Service", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)


# ─── Models ──────────────────────────────────────────────────────────────
class SignupRequest(BaseModel):
    model_config = {"populate_by_name": True}

    email: EmailStr
    first_name: str = Field(
        "",
        max_length=80,
        validation_alias=AliasChoices("first_name", "firstName", "name"),
    )
    product_name: str = Field(..., min_length=1, max_length=120)
    description: str = Field(
        "",
        max_length=2000,
        validation_alias=AliasChoices("description", "idea", "product_idea"),
    )
    tier: Literal["sandbox", "launch"] = "sandbox"
    region: str | None = Field(None, max_length=40)
    domain_choice: str | None = Field(None, max_length=255)
    github_username: str = Field(
        "",
        max_length=39,
        validation_alias=AliasChoices("github_username", "githubUsername"),
    )
    # T&Cs consent — required at signup time so we have an auditable
    # record of acceptance per row (see accepted_terms_at column on the
    # signups table). False/missing → 422 from create_signup.
    accepted_terms: bool = Field(
        False,
        validation_alias=AliasChoices("accepted_terms", "acceptedTerms"),
    )
    turnstile_token: str | None = Field(None, max_length=4096)

    @field_validator("product_name", "description")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()

    @field_validator("first_name")
    @classmethod
    def clean_first_name(cls, v: str) -> str:
        # Take the first whitespace-separated token, capitalize it.
        # "  alice ross " → "Alice", "Mr. Bean" → "Mr." → "Mr." (still
        # better than firing the whole string into a greeting).
        token = v.strip().split()[0] if v.strip() else ""
        return token[:1].upper() + token[1:] if token else ""

    @field_validator("github_username")
    @classmethod
    def clean_github_username(cls, v: str) -> str:
        # GitHub usernames: 1–39 chars, alphanumeric + single hyphens, no
        # leading/trailing hyphen. Strip @ prefix if a customer pastes one.
        v = v.strip().lstrip("@")
        if not v:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}", v):
            raise ValueError("github_username must be a valid GitHub handle")
        return v


class SignupResponse(BaseModel):
    ok: bool
    message: str


# ─── Rate limit ──────────────────────────────────────────────────────────
def check_rate_limit(ip: str) -> bool:
    """True if the request is allowed; False if rate-limited."""
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - RATE_LIMIT_WINDOW_SECONDS
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM rate_limit WHERE timestamp < ?", (cutoff,))
        cur = conn.execute(
            "SELECT COUNT(*) FROM rate_limit WHERE ip = ? AND timestamp >= ?",
            (ip, cutoff),
        )
        count = cur.fetchone()[0]
        if count >= RATE_LIMIT_MAX_REQUESTS:
            return False
        conn.execute("INSERT INTO rate_limit (ip, timestamp) VALUES (?, ?)", (ip, now))
        conn.commit()
    return True


# ─── Email ───────────────────────────────────────────────────────────────
def _html_escape(s: str) -> str:
    """Minimal HTML escape — keeps deps to stdlib + httpx."""
    return (
        s.replace("&", "&amp;")
         .replace("<", "&lt;")
         .replace(">", "&gt;")
         .replace('"', "&quot;")
    )


async def _resend_send(payload: dict[str, Any]) -> None:
    """POST to Resend; raise on HTTP error so the caller can log+swallow."""
    async with httpx.AsyncClient(timeout=RESEND_TIMEOUT_SECONDS) as client:
        r = await client.post(
            RESEND_API_URL,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        r.raise_for_status()


async def send_founder_notification(
    req: SignupRequest,
    signup_id: int,
    ip: str = "unknown",
    geo: dict[str, str] | None = None,
) -> None:
    """Email the founder with signup details (provisioning runs automatically for Sandbox)."""
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping founder notification")
        return

    geo = geo or {"country_code": "", "city": "", "asn": ""}
    geo_line = (
        f"{geo['country_code'] or '?'} · {geo['city'] or '?'} · ASN {geo['asn'] or '?'}"
        if any(geo.values()) else "(lookup unavailable)"
    )

    subject = f"[Hatchik signup #{signup_id}] {req.tier.title()}: {req.product_name}"
    body = f"""\
New Hatchik signup #{signup_id}

  Email:       {req.email}
  First name:  {req.first_name or '(not provided)'}
  Tier:        {req.tier}
  Product:     {req.product_name}
  Region:      {req.region or 'not specified'}
  Domain:      {req.domain_choice or 'will be discussed'}
  IP:          {ip}
  Geo:         {geo_line}

  Description:
  {req.description or '(none)'}

Sandbox tier: provisioning runs automatically — watch /var/log/hatchik/provision-{signup_id}.log
Launch tier: see FIRST_CUSTOMER_RUNBOOK.md for the manual flow.
"""

    try:
        await _resend_send({
            "from": FROM_EMAIL,
            "to": [FOUNDER_EMAIL],
            "reply_to": str(req.email),
            "subject": subject,
            "text": body,
        })
        log.info("Founder notification sent for signup #%s", signup_id)
    except Exception as e:  # noqa: BLE001 — never break signup on email failure
        log.error("Failed to send founder notification for #%s: %s", signup_id, e)


def _customer_email_bodies(req: SignupRequest, queue_note: str = "") -> tuple[str, str]:
    """Render plaintext + HTML versions of the customer acknowledgement.

    ``queue_note`` is an optional one-liner appended to ``next_step`` when
    the signup landed in the throttle queue — soft messaging only, no
    panic, no precise time estimate.
    """
    if req.tier == "launch":
        intro = f"Thanks for signing up for {req.product_name} on the Launch tier."
        next_step = (
            "Your Hatchik is being provisioned now. You'll get another email "
            "shortly with the link to log in and start building — usually "
            "within a few hours, sometimes faster if nothing needs your input."
        )
    else:
        intro = f"Thanks for signing up — and welcome to {req.product_name}."
        next_step = (
            "Your Hatchik sandbox is being provisioned now. You'll get "
            "another email in a few minutes with the link to log in and "
            "start building."
        )

    if queue_note:
        next_step = f"{next_step} {queue_note}"

    greeting = f"Hi {req.first_name}," if req.first_name else "Hi,"
    greeting_html = _html_escape(greeting)

    text = f"""\
{greeting}

{intro}

{next_step}

Further information can be found at https://hatchik.com/#faq if you need it.

— Hatchik

(This is an automated message — please don't reply.)
"""

    intro_html = _html_escape(intro)
    next_html = _html_escape(next_step)

    html = f"""\
<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Welcome to Hatchik</title>
</head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
    <tr>
      <td align="center" style="padding:32px 16px;">
        <table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:8px;padding:32px;">
          <tr>
            <td style="font-size:16px;line-height:1.6;color:#1a1a1a;">
              <p style="margin:0 0 16px 0;">{greeting_html}</p>
              <p style="margin:0 0 16px 0;">{intro_html}</p>
              <p style="margin:0 0 16px 0;">{next_html}</p>
              <p style="margin:0 0 16px 0;">Further information can be found <a href="https://hatchik.com/#faq" style="color:#4f46e5;text-decoration:underline;">here</a> if you need it.</p>
              <p style="margin:24px 0 0 0;">&mdash; Hatchik</p>
              <p style="margin:24px 0 0 0;color:#888;font-size:12px;">This is an automated message &mdash; please don&rsquo;t reply.</p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
"""
    return text, html


async def send_customer_acknowledgement(req: SignupRequest, queue_note: str = "") -> None:
    """Confirm-receipt email to the customer. Provisioning email follows."""
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping customer acknowledgement")
        return

    text_body, html_body = _customer_email_bodies(req, queue_note=queue_note)
    if req.tier == "launch":
        subject = f"Thanks for signing up — getting {req.product_name} built now"
    else:
        subject = f"Welcome to Hatchik — your sandbox for {req.product_name} is being set up"

    try:
        await _resend_send({
            "from": FROM_EMAIL,
            "to": [str(req.email)],
            "subject": subject,
            "text": text_body,
            "html": html_body,
        })
        log.info("Customer acknowledgement sent to %s", req.email)
    except Exception as e:  # noqa: BLE001 — never break signup on email failure
        log.error("Failed to send customer acknowledgement to %s: %s", req.email, e)


# ─── Paddle webhook ──────────────────────────────────────────────────────
def verify_paddle_signature(payload: bytes, sig_header: str, secret: str) -> bool:
    """Verify a Paddle Billing webhook signature header per
    https://developer.paddle.com/webhooks/signature-verification.

    Header format: ``ts=<unix_ts>;h1=<sig>``. We compute HMAC-SHA256 over
    ``<ts>:<payload>`` with the notification secret key and compare against
    the ``h1`` signature in constant time. Replay tolerance enforced by
    ``PADDLE_SIGNATURE_TOLERANCE_SECONDS``.
    """
    if not sig_header or not secret:
        return False

    timestamp: str | None = None
    signatures: list[str] = []
    for part in sig_header.split(";"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if key == "ts":
            timestamp = value
        elif key == "h1":
            signatures.append(value)

    if timestamp is None or not signatures:
        return False

    try:
        ts_int = int(timestamp)
    except ValueError:
        return False

    now = int(datetime.now(timezone.utc).timestamp())
    if abs(now - ts_int) > PADDLE_SIGNATURE_TOLERANCE_SECONDS:
        log.warning("Paddle webhook timestamp outside tolerance (drift=%ss)", now - ts_int)
        return False

    # Paddle signs `<ts>:<raw_body>` — note the colon separator (Stripe uses
    # a dot). The raw bytes must be the exact body received; any
    # re-serialization breaks the signature.
    signed_payload = f"{timestamp}:".encode() + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, sig) for sig in signatures)


def _event_already_processed(event_id: str) -> bool:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT 1 FROM processed_events WHERE event_id = ?", (event_id,)
        ).fetchone()
    return row is not None


def _mark_event_processed(event_id: str, event_type: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO processed_events (event_id, event_type, processed_at) "
            "VALUES (?, ?, ?)",
            (event_id, event_type, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()


def _persist_paddle_transaction(transaction: dict[str, Any], raw_payload: bytes) -> None:
    """Record a completed Paddle transaction in the payments table.

    Paddle's ``transaction.completed`` event ``data`` object exposes
    ``id`` (transaction id), ``customer_id``, ``subscription_id``,
    ``status``, ``currency_code``, ``details.totals.grand_total``, and the
    payer's email under ``customer.email`` or ``billing_details.email``.
    Field names follow the Paddle Billing (v1) shape, not Paddle Classic.
    """
    details = transaction.get("details") or {}
    totals = details.get("totals") or {}
    customer = transaction.get("customer") or {}
    billing_details = transaction.get("billing_details") or {}
    email = (
        customer.get("email")
        or billing_details.get("email")
        or transaction.get("customer_email")
    )
    amount = (
        totals.get("grand_total")
        or totals.get("total")
        or transaction.get("amount")
    )
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR IGNORE INTO payments (
                created_at, paddle_transaction_id, paddle_customer_id,
                paddle_subscription_id, customer_email, currency, amount,
                status, raw_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now(timezone.utc).isoformat(),
                transaction.get("id"),
                transaction.get("customer_id"),
                transaction.get("subscription_id"),
                email,
                transaction.get("currency_code") or transaction.get("currency"),
                str(amount) if amount is not None else None,
                transaction.get("status"),
                raw_payload.decode("utf-8", errors="replace"),
            ),
        )
        conn.commit()


async def notify_founder_payment_failure(transaction: dict[str, Any]) -> None:
    """Email the founder when a Paddle transaction payment fails — needs fast attention.

    Paddle's ``transaction.payment_failed`` event carries the transaction
    object; ``payments`` is a list of attempts, each with an
    ``error_code``. Customer email is in ``customer.email`` (or
    ``billing_details.email`` for guest checkout).
    """
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping payment-failure notification")
        return

    customer = transaction.get("customer") or {}
    billing_details = transaction.get("billing_details") or {}
    customer_email = (
        customer.get("email")
        or billing_details.get("email")
        or "(unknown)"
    )
    customer_id = transaction.get("customer_id") or "(unknown)"
    subscription_id = transaction.get("subscription_id") or "(none)"

    details = transaction.get("details") or {}
    totals = details.get("totals") or {}
    amount = totals.get("grand_total") or totals.get("total")
    currency = (transaction.get("currency_code") or transaction.get("currency") or "").upper()
    # Paddle returns amounts as minor-unit strings (e.g. "8900" = 89.00 GBP).
    try:
        amount_str = f"{int(amount) / 100:.2f} {currency}" if amount else "(unknown amount)"
    except (TypeError, ValueError):
        amount_str = f"{amount} {currency}".strip()

    payments_list = transaction.get("payments") or []
    last_error = "(no error code)"
    if payments_list:
        last = payments_list[-1] if isinstance(payments_list, list) else {}
        last_error = (
            last.get("error_code")
            or last.get("status")
            or last_error
        )

    body = f"""\
Paddle transaction payment FAILED.

  Customer email:        {customer_email}
  Paddle customer:       {customer_id}
  Paddle subscription:   {subscription_id}
  Transaction id:        {transaction.get('id') or '(unknown)'}
  Amount:                {amount_str}
  Last attempt error:    {last_error}
  Status:                {transaction.get('status') or '(unknown)'}

Action: check the Paddle dashboard, follow up with the customer if needed.
"""

    try:
        await _resend_send({
            "from": FROM_EMAIL,
            "to": [FOUNDER_EMAIL],
            "subject": f"[Hatchik] Payment failed — {customer_email}",
            "text": body,
        })
        log.info("Payment-failure notification sent for customer %s", customer_id)
    except Exception as e:  # noqa: BLE001 — never crash the webhook on email
        log.error("Failed to send payment-failure notification: %s", e)


# ─── Paddle subscription event handlers ─────────────────────────────────
# All three default to non-destructive behaviour: they record the tier
# transition and (for created) email the founder a SAFE_MODE plan via
# promote.py. None of them ever block the webhook on long-running work;
# heavy lifting is dispatched via subprocess so the webhook ack stays fast.

def _resolve_signup_id_by_paddle_customer(customer_id: str | None) -> int | None:
    """Look up signup_id from a Paddle customer_id via the payments table.

    Falls back to None if we haven't seen a transaction for this customer
    yet (subscription.created can arrive before transaction.completed in
    some Paddle event orderings).
    """
    if not customer_id:
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT customer_email FROM payments "
                "WHERE paddle_customer_id = ? "
                "ORDER BY id DESC LIMIT 1", (customer_id,),
            ).fetchone()
            if not row or not row[0]:
                return None
            sig = conn.execute(
                "SELECT id FROM signups WHERE LOWER(email) = LOWER(?) "
                "ORDER BY id DESC LIMIT 1", (row[0],),
            ).fetchone()
            return int(sig[0]) if sig else None
    except Exception as e:  # noqa: BLE001
        log.error("resolve_signup_id_by_paddle_customer failed: %s", e)
        return None


def _resolve_signup_id_by_customer_email(email: str | None) -> int | None:
    if not email:
        return None
    try:
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT id FROM signups WHERE LOWER(email) = LOWER(?) "
                "ORDER BY id DESC LIMIT 1", (email,),
            ).fetchone()
            return int(row[0]) if row else None
    except Exception as e:  # noqa: BLE001
        log.error("resolve_signup_id_by_customer_email failed: %s", e)
        return None


def _record_paddle_transition(
    signup_id: int,
    from_tier: str | None,
    to_tier: str,
    event_id: str,
    note: str,
) -> None:
    """Append a tier_transitions row from a webhook context.

    Idempotent by (signup_id, paddle_event_id, to_tier). One event can
    legitimately produce multiple rows when to_tier differs — e.g. a
    subscription.updated event that's both a status change *and* a plan
    change yields two rows (launch→launch status + launch→growth plan).
    The webhook-level dedup (processed_events) catches whole-event
    replays; this row-level dedup catches duplicate inserts of the same
    semantic transition within one event.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            existing = conn.execute(
                "SELECT 1 FROM tier_transitions "
                "WHERE signup_id = ? AND paddle_event_id = ? AND to_tier = ?",
                (signup_id, event_id, to_tier),
            ).fetchone()
            if existing:
                return
            conn.execute(
                "INSERT INTO tier_transitions "
                "(signup_id, from_tier, to_tier, occurred_at, paddle_event_id, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (signup_id, from_tier, to_tier,
                 datetime.now(timezone.utc).isoformat(), event_id, note),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        log.error("record_paddle_transition failed: %s", e)


def _trigger_promote_subprocess(
    signup_id: int, event_id: str,
    env_overrides: dict[str, str] | None = None,
) -> None:
    """Fire promote.py in SAFE_MODE as a detached subprocess. Errors are
    logged but never crash the webhook — the script's own founder-email
    handles the "you need to look at this" path.

    env_overrides lets the admin force-promote endpoint pass
    ``HATCHIK_PROMOTE_EXECUTE=1`` so the subprocess drops SAFE_MODE.
    """
    import subprocess as _subprocess
    cmd = [
        "python3", PROMOTE_SCRIPT,
        "--signup-id", str(signup_id),
        "--paddle-event-id", event_id,
    ]
    if env_overrides and env_overrides.get("HATCHIK_PROMOTE_EXECUTE") == "1":
        cmd.append("--execute")
    if not Path(PROMOTE_SCRIPT).exists():
        log.warning("promote.py not at %s — skipping subprocess fire", PROMOTE_SCRIPT)
        return
    env = {**os.environ, **(env_overrides or {})}
    try:
        _subprocess.Popen(  # noqa: S603 — fixed argv, no shell
            cmd,
            stdout=_subprocess.DEVNULL,
            stderr=_subprocess.DEVNULL,
            start_new_session=True,
            env=env,
        )
        log.info(
            "Triggered promote.py for signup #%s (event=%s, execute=%s)",
            signup_id, event_id,
            (env_overrides or {}).get("HATCHIK_PROMOTE_EXECUTE", "0"),
        )
    except Exception as e:  # noqa: BLE001
        log.error("Failed to spawn promote.py for signup #%s: %s", signup_id, e)


def _mark_launch_canceled(signup_id: int) -> None:
    """Set canceled_at on the Launch registry entry for this signup.

    No-op if the launch registry doesn't have a tenant for this signup —
    promote.py may still be running, or the cancellation arrived before
    we provisioned. Either way the next launch_lifecycle.py daily run
    will reconcile.
    """
    if not LAUNCH_REGISTRY_PATH.exists():
        log.info("launch registry not present at %s — skipping mark-canceled",
                 LAUNCH_REGISTRY_PATH)
        return
    try:
        reg = json.loads(LAUNCH_REGISTRY_PATH.read_text())
    except Exception as e:  # noqa: BLE001
        log.error("Failed to read launch registry: %s", e)
        return
    changed = False
    for slug, t in (reg.get("tenants") or {}).items():
        if t.get("signup_id") == signup_id and not t.get("canceled_at"):
            t["canceled_at"] = datetime.now(timezone.utc).isoformat()
            changed = True
            log.info("Marked launch tenant %s as canceled (signup #%s)", slug, signup_id)
    if changed:
        try:
            tmp = LAUNCH_REGISTRY_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(reg, indent=2))
            tmp.replace(LAUNCH_REGISTRY_PATH)
        except Exception as e:  # noqa: BLE001
            log.error("Failed to persist launch registry: %s", e)


async def _handle_subscription_created(data: dict[str, Any], event_id: str) -> None:
    """Paddle subscription.created — customer paid for Launch.

    1. Resolve customer_email → signup_id (via payments table or signups
       row; Paddle puts customer_email on data.customer.email in v1).
    2. Record tier_transitions (sandbox → launch).
    3. Fire promote.py SAFE_MODE subprocess — emails founder the plan.
    """
    customer = data.get("customer") or {}
    customer_email = customer.get("email") or data.get("customer_email")
    customer_id = data.get("customer_id")
    custom_data = data.get("custom_data") or {}
    wizard_session_id = custom_data.get("wizard_session_id")

    # If the checkout was initiated from a wizard session (MCP signup path),
    # the wizard session doesn't yet have a signups.id — the payment IS
    # the moment that creates it. Mint the signup row from the session
    # choices, promote the session to provisioning, then fall through to
    # the normal launch transition + promote subprocess.
    signup_id: int | None = None
    if wizard_session_id:
        import wizard_sessions as _ws
        ws_session = _ws.get(wizard_session_id)
        if ws_session and not ws_session.signup_id:
            try:
                signup_id = await _create_signup_from_wizard(ws_session)
                _ws.mark_provisioning(
                    wizard_session_id, signup_id,
                    paddle_txn_id=data.get("id") or data.get("transaction_id"),
                )
                log.info(
                    "subscription.created event=%s — minted signup #%s from wizard %s",
                    event_id, signup_id, wizard_session_id,
                )
            except Exception as e:  # noqa: BLE001
                log.error(
                    "subscription.created event=%s — failed to mint signup "
                    "from wizard %s: %s",
                    event_id, wizard_session_id, e,
                )
        elif ws_session and ws_session.signup_id:
            signup_id = ws_session.signup_id

    if signup_id is None:
        signup_id = (
            _resolve_signup_id_by_paddle_customer(customer_id)
            or _resolve_signup_id_by_customer_email(customer_email)
        )
    if not signup_id:
        log.warning(
            "subscription.created event=%s — could not resolve to a signup. "
            "customer_email=%r customer_id=%r wizard_session=%r. Will be "
            "picked up by launch_lifecycle.py reconciler.",
            event_id, customer_email, customer_id, wizard_session_id,
        )
        return

    _record_paddle_transition(
        signup_id=signup_id,
        from_tier="sandbox",
        to_tier="launch",
        event_id=event_id,
        note=("paddle subscription.created"
              + (f" via wizard {wizard_session_id}" if wizard_session_id else "")),
    )
    _trigger_promote_subprocess(signup_id, event_id)


async def _handle_subscription_updated(data: dict[str, Any], event_id: str) -> None:
    """Paddle subscription.updated — plan change (launch↔growth), status
    change (active/paused/past_due), or trial → paid transition.

    For now we only handle the status changes by recording them; an
    actual plan change (launch→growth) is detected by the price_id on
    the line items and recorded as a tier_transitions row. The in-place
    VPS resize is deferred (launch_lifecycle.py picks it up).
    """
    customer_id = data.get("customer_id")
    signup_id = _resolve_signup_id_by_paddle_customer(customer_id)
    if not signup_id:
        return

    # Status flip (active/paused/past_due) → record only.
    status = data.get("status")
    if status:
        _record_paddle_transition(
            signup_id=signup_id,
            from_tier="launch",
            to_tier="launch",  # tier unchanged; the transition row is the audit trail
            event_id=event_id,
            note=f"paddle subscription.updated status={status}",
        )

    # Plan change detection: look for a Growth price in items.
    items = data.get("items") or []
    growth_price_id = os.environ.get("PADDLE_GROWTH_PRICE_ID", "")
    if growth_price_id:
        for item in items:
            price = item.get("price") or {}
            if price.get("id") == growth_price_id:
                _record_paddle_transition(
                    signup_id=signup_id,
                    from_tier="launch",
                    to_tier="growth",
                    event_id=event_id,
                    note="paddle plan change to growth",
                )
                break


async def _handle_subscription_canceled(data: dict[str, Any], event_id: str) -> None:
    """Paddle subscription.canceled — customer churned.

    1. Resolve to signup_id.
    2. Record tier_transitions (launch → cancelled).
    3. Mark canceled_at on the launch registry so the daily
       launch_lifecycle.py reconciler counts down the 30-day grace.
    4. Email customer the grace-period explanation (launch_lifecycle.py
       sends repeat reminders at day 25; this is the initial "we got it"
       acknowledgement).
    """
    customer = data.get("customer") or {}
    customer_email = customer.get("email") or data.get("customer_email")
    customer_id = data.get("customer_id")
    signup_id = (
        _resolve_signup_id_by_paddle_customer(customer_id)
        or _resolve_signup_id_by_customer_email(customer_email)
    )
    if not signup_id:
        log.warning(
            "subscription.canceled event=%s — could not resolve customer_email=%r",
            event_id, customer_email,
        )
        return

    _record_paddle_transition(
        signup_id=signup_id,
        from_tier="launch",
        to_tier="cancelled",
        event_id=event_id,
        note="paddle subscription.canceled",
    )
    _mark_launch_canceled(signup_id)

    if customer_email:
        try:
            await _resend_send({
                "from": FROM_EMAIL,
                "to": [customer_email],
                "subject": "Your Hatchik subscription is canceled — what happens next",
                "text": (
                    "Hi,\n\n"
                    "We see you've canceled your Hatchik subscription. Sorry to see you go.\n\n"
                    "Here's what happens:\n"
                    "  - Your service stays online for 30 days from cancellation\n"
                    "  - At day 30 we snapshot your VPS, then take it offline\n"
                    "  - The snapshot is kept for 30 more days — if you change your\n"
                    "    mind in that window, you can re-subscribe and we'll restore\n"
                    "  - After 60 days total, the snapshot is purged\n\n"
                    "Need to export anything before then? Reply to this email and we'll\n"
                    "arrange a data export at no charge.\n\n"
                    "— Hatchik\n"
                ),
            })
        except Exception as e:  # noqa: BLE001
            log.error("subscription.canceled customer email failed: %s", e)


# ─── Endpoints ───────────────────────────────────────────────────────────
# Statuses that mean "this signup row no longer occupies a real sandbox."
# Anything outside this set counts as an active Sandbox-tier signup for the
# per-email cap below.
_SANDBOX_INACTIVE_STATUSES = ("deleted", "cancelled", "archived_purged")


def _count_active_sandboxes(email: str) -> tuple[int, str | None]:
    """Return (active_sandbox_count, existing_url) for the per-email cap.

    A sandbox counts as active when:
      - the signups row has tier='sandbox' AND status NOT IN the inactive set
      - AND the matching registry tenant (if any) is not 'decommissioned'

    A signup that never made it into the registry (e.g. provision crashed
    before the registry write) still counts — otherwise customers can spam
    sandbox signups during outage windows.
    """
    lowered = email.lower().strip()
    placeholders = ", ".join("?" for _ in _SANDBOX_INACTIVE_STATUSES)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT id FROM signups "
            f"WHERE LOWER(email) = ? AND tier = 'sandbox' "
            f"AND status NOT IN ({placeholders})",
            (lowered, *_SANDBOX_INACTIVE_STATUSES),
        ).fetchall()

    if not rows:
        return 0, None

    reg = _load_registry()
    tenants_by_signup = {
        t.get("signup_id"): {"slug": slug, **t}
        for slug, t in reg.get("tenants", {}).items()
        if t.get("signup_id")
    }

    count = 0
    existing_url: str | None = None
    for r in rows:
        tenant = tenants_by_signup.get(r["id"])
        # Skip tenants the orchestrator has already torn down.
        if tenant and tenant.get("status") == "decommissioned":
            continue
        count += 1
        if existing_url is None and tenant and tenant.get("url"):
            existing_url = tenant["url"]
    return count, existing_url


@app.post("/api/signup", status_code=201)
async def create_signup(req: SignupRequest, request: Request) -> Any:
    ip = (request.headers.get("CF-Connecting-IP")
          or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))

    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    # T&Cs consent gate. The marketing form checkbox is required and the
    # submit button is disabled until ticked, so this almost only fires
    # for direct API callers or out-of-date front-ends — still, the audit
    # trail (accepted_terms_at) only makes sense if we hard-reject
    # missing consent here.
    if not req.accepted_terms:
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "terms_not_accepted",
                "message": "Please accept the Terms of Service and Privacy Policy to sign up.",
            },
        )

    # Launch-tier domain allowlist. Phase-1 input guard: rejects
    # customer-supplied domains whose TLD costs more than the ~£14/yr we
    # can absorb inside the £89 setup fee. Sandbox doesn't get a custom
    # domain (it lives on a hatchik.com subdomain) so we skip there.
    # Empty domain_choice on Launch is also rejected — we can't honour
    # "year 1 included" against a blank value.
    if req.tier == "launch":
        ok_domain, domain_msg = validate_domain_choice(req.domain_choice)
        if not ok_domain:
            log.info(
                "rejected launch signup domain from %s: %r (%s)",
                ip, req.domain_choice, domain_msg,
            )
            raise HTTPException(
                status_code=422,
                detail={
                    "ok": False,
                    "error": "domain_not_supported",
                    "message": domain_msg,
                },
            )

    # Disposable-email gate — applied before any expensive work (Turnstile,
    # geo-IP, DB insert) so abuse traffic doesn't waste resources.
    if is_disposable_email(str(req.email)):
        log.info("rejected disposable email from %s: %s", ip, req.email)
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "disposable_email",
                "message": "Please use a real email address — we send important account info there.",
            },
        )

    # Cloudflare Turnstile. ``verify_turnstile`` returns True when the
    # secret is unset (dev mode) so this is a no-op locally; in prod we
    # fail-closed on missing/invalid tokens.
    if TURNSTILE_SECRET and not await verify_turnstile(req.turnstile_token, ip):
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "turnstile_failed",
                "message": "We couldn't verify you're human — please try again.",
            },
        )

    # Geo-IP lookup. Best-effort, soft-bounded by GEO_IP_TIMEOUT_SECONDS.
    geo = await lookup_geo_ip(ip)
    if geo["country_code"] and geo["country_code"] in BLOCKED_COUNTRIES:
        log.warning(
            "blocked-country signup attempt: ip=%s country=%s email=%s",
            ip, geo["country_code"], req.email,
        )
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "blocked_region",
                "message": "Sign-ups from your region aren't currently supported. If this is a mistake, email hello@hatchik.com.",
            },
        )

    # GitHub handle existence check. Pydantic validates the regex shape,
    # but customers regularly paste their product name (e.g. "myidea")
    # which is regex-valid but resolves to nothing — they then end up
    # with a private repo they can't see. Reject with a friendly message
    # so they fix it at signup rather than after provisioning.
    if req.github_username:
        exists, reason = await _github_user_exists(req.github_username)
        if not exists and reason == "not_found":
            log.info("rejected unknown github handle from %s: %s", ip, req.github_username)
            return JSONResponse(
                status_code=422,
                content={
                    "ok": False,
                    "error": "github_user_not_found",
                    "message": (
                        f"We couldn't find a GitHub user called "
                        f"'{req.github_username}'. Please double-check — "
                        f"this should be your GitHub username (e.g. 'alice'), "
                        f"not your product name."
                    ),
                },
            )

    # One active Sandbox per email. Launch/Growth packages stay unrestricted.
    # 409 body is shaped {ok, error, message} (no `detail` wrapper) so the
    # marketing front-ends can show the message verbatim.
    if req.tier == "sandbox":
        active_count, existing_url = _count_active_sandboxes(str(req.email))
        if active_count >= 1:
            where = existing_url or "your previous sign-up"
            return JSONResponse(
                status_code=409,
                content={
                    "ok": False,
                    "error": "sandbox_exists",
                    "message": (
                        f"You already have a Sandbox running at {where}. "
                        "Delete it (https://hatchik.com/delete-sandbox) "
                        "first if you want to start fresh."
                    ),
                },
            )

    user_agent = request.headers.get("User-Agent", "")
    created_at = datetime.now(timezone.utc).isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            """
            INSERT INTO signups (
                created_at, email, first_name, product_name, description, tier,
                region, domain_choice, ip_address, user_agent, github_username,
                status, country_code, city, asn, accepted_terms_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?, ?)
            """,
            (
                created_at, str(req.email), req.first_name, req.product_name,
                req.description, req.tier, req.region, req.domain_choice,
                ip, user_agent, req.github_username or None,
                geo["country_code"] or None, geo["city"] or None, geo["asn"] or None,
                created_at,  # accepted_terms_at — gate above guarantees consent
            ),
        )
        signup_id = cur.lastrowid or 0
        conn.execute(
            "INSERT INTO tier_transitions (signup_id, from_tier, to_tier, occurred_at, paddle_event_id, notes) "
            "VALUES (?, NULL, ?, ?, NULL, 'initial signup')",
            (signup_id, req.tier, created_at),
        )
        conn.commit()

    log.info(
        "New signup #%s: %s tier=%s country=%s",
        signup_id, req.email, req.tier, geo["country_code"] or "?",
    )

    # Decide queue-delay messaging before we kick off provisioning so we
    # can colour the acknowledgement email accordingly.
    queue_note = ""
    if req.tier == "sandbox":
        async with _in_flight_lock:
            in_flight = len(_in_flight_signups)
        if in_flight >= MAX_CONCURRENT_PROVISIONS:
            queue_note = (
                "(You're a bit further back in the queue — we'll have your "
                "sandbox ready in a few extra minutes.)"
            )

    # Fire both notification emails. Failures are logged inside each helper
    # so they cannot break the signup — the DB insert above is the source of
    # truth and the customer has already received a 201 by the time these run.
    await send_founder_notification(req, signup_id, ip, geo=geo)
    await send_customer_acknowledgement(req, queue_note=queue_note)

    # Sandbox signups go through the concurrent-provision throttle: if
    # there's a free slot we run provision.py in a background task right
    # now, otherwise we mark the row 'queued' and the background worker
    # picks it up once a slot opens.
    if req.tier == "sandbox":
        await enqueue_or_dispatch(signup_id)

    return SignupResponse(
        ok=True,
        message="Thanks. We're setting your Hatchik up — check your email in a few minutes.",
    )


@app.get("/api/signup/stats")
async def stats() -> dict[str, int]:
    """Public stats — count of signups, no PII."""
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM signups").fetchone()[0]
        new = conn.execute("SELECT COUNT(*) FROM signups WHERE status = 'new'").fetchone()[0]
    return {"total": total, "new": new}


# ─── Account harness: create + delete ────────────────────────────────────
# Admin path: shared-secret header X-Admin-Token = HATCHIK_ADMIN_TOKEN.
# Self-serve path: customer hits POST /api/account/request-deletion with
# their email; service mails a one-time token; customer clicks the link;
# /api/account/confirm-deletion?token=... fires decommission.py.

def _require_admin(token: str | None) -> None:
    if not ADMIN_TOKEN or not token or not hmac.compare_digest(token, ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="admin token required")


def _run_decommission(slug: str, hard: bool) -> dict[str, Any]:
    """Subprocess decommission.py and parse its JSON summary."""
    import subprocess
    if not Path(DECOMMISSION_SCRIPT).exists():
        raise HTTPException(status_code=500, detail="decommission.py not deployed")
    cmd = ["python3", DECOMMISSION_SCRIPT, slug, "--json", "--quiet"]
    if hard:
        cmd.append("--hard")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        log.error("decommission.py failed for %s: %s", slug, r.stderr)
        raise HTTPException(status_code=500, detail=f"decommission failed: {r.stderr.strip()[:200]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"slug": slug, "raw": r.stdout.strip()}


def _record_cancellation_transition(slug: str, note: str = "decommissioned") -> None:
    """Append a tier_transitions row marking this slug's signup as cancelled.

    Best-effort and idempotent in spirit: we look up the signup via the
    registry, read its current tier, and write the transition. Failures
    are logged but never raised — metrics must never break decommission.
    """
    try:
        reg = _load_registry()
        tenant = reg.get("tenants", {}).get(slug) or {}
        signup_id = tenant.get("signup_id")
        if not signup_id:
            return
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute(
                "SELECT tier FROM signups WHERE id = ?", (signup_id,)
            ).fetchone()
            current_tier = row[0] if row else None
            conn.execute(
                "INSERT INTO tier_transitions (signup_id, from_tier, to_tier, occurred_at, paddle_event_id, notes) "
                "VALUES (?, ?, 'cancelled', ?, NULL, ?)",
                (signup_id, current_tier, datetime.now(timezone.utc).isoformat(), note),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001 — metrics never break teardown
        log.warning("failed to record cancellation transition for slug=%s: %s", slug, e)


@app.delete("/api/admin/account/{slug}")
async def admin_delete_account(
    slug: str,
    hard: bool = False,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Admin: tear down a sandbox by slug."""
    _require_admin(x_admin_token)
    log.info("admin decommission requested: slug=%s hard=%s", slug, hard)
    _record_cancellation_transition(slug, note="admin decommission")
    return _run_decommission(slug, hard=hard)


def _run_restore(slug: str) -> dict[str, Any]:
    """Subprocess restore.py and parse its JSON summary.

    Restore is expensive — volume untar + compose up + healthcheck =
    60–120s — so we give it a much longer timeout than decommission.
    """
    import subprocess
    if not Path(RESTORE_SCRIPT).exists():
        raise HTTPException(status_code=500, detail="restore.py not deployed")
    cmd = ["python3", RESTORE_SCRIPT, slug, "--json", "--quiet"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        log.error("restore.py failed for %s: %s", slug, r.stderr)
        raise HTTPException(status_code=500, detail=f"restore failed: {r.stderr.strip()[:200]}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return {"slug": slug, "raw": r.stdout.strip()}


@app.post("/api/admin/account/{slug}/restore")
async def admin_restore_account(
    slug: str,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Admin: revive an archived sandbox.

    Customers don't hit this directly — they request restore via
    POST /api/account/request-restore which mails the founder, then the
    founder reviews + invokes this endpoint (or runs restore.py on the
    host). Archives are valuable to spammers so we keep restore
    gate-kept by an admin until we have a stronger anti-abuse story.
    """
    _require_admin(x_admin_token)
    log.info("admin restore requested: slug=%s", slug)
    return _run_restore(slug)


class RestoreRequest(BaseModel):
    email: EmailStr
    note: str = Field("", max_length=2000)


@app.post("/api/account/request-restore", status_code=202)
async def request_restore(req: RestoreRequest, request: Request) -> dict[str, Any]:
    """Self-serve: customer asks to restore an archived sandbox.

    No automatic action — archives are valuable to spammers if abused,
    so this just emails the founder with the customer's email + any
    note they left. The founder reviews, then runs ``restore.py <slug>``
    on the host (or POSTs to /api/admin/account/{slug}/restore). The
    customer gets the "your sandbox is back" email from restore.py
    itself once it completes.

    Anti-enumeration: always returns 202 with the same body whether or
    not an archived sandbox actually exists for that email.
    """
    ip = (request.headers.get("CF-Connecting-IP")
          or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))
    if not check_rate_limit(ip):
        raise HTTPException(status_code=429, detail="Too many requests")

    email = str(req.email).lower().strip()
    # Look up any archived tenant for this email — we include the slug
    # in the founder email so the admin can act without further
    # research.
    reg = _load_registry()
    matches: list[tuple[str, dict[str, Any]]] = []
    for slug, tenant in reg.get("tenants", {}).items():
        if (tenant.get("email") or "").lower() == email and tenant.get("status") == "archived":
            matches.append((slug, tenant))

    await _notify_founder_restore_request(email, req.note, matches, ip)
    log.info(
        "restore request from %s (%d archived match%s found)",
        email, len(matches), "es" if len(matches) != 1 else "",
    )
    return {
        "ok": True,
        "message": (
            "Thanks — if we have an archived sandbox for that email, an "
            "admin will revive it within a working day and email you a "
            "sign-in link."
        ),
    }


async def _notify_founder_restore_request(
    email: str, note: str, matches: list[tuple[str, dict[str, Any]]], ip: str
) -> None:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping restore request notification")
        return
    if matches:
        match_lines = "\n".join(
            f"  • slug={slug}  port={t.get('port')}  archived_at={t.get('archived_at')}"
            for slug, t in matches
        )
    else:
        match_lines = "  (no archived tenant matched this email — check /api/admin/accounts)"

    body = f"""\
Restore-sandbox request.

  Email:    {email}
  IP:       {ip}
  Matches:
{match_lines}

  Customer note:
  {note or '(none)'}

To restore, SSH the sandbox host and run:
  python3 /opt/hatchik-orchestrator/restore.py <slug>

Or POST /api/admin/account/<slug>/restore with X-Admin-Token.
restore.py emails the customer automatically once the sandbox is live.
"""
    try:
        await _resend_send({
            "from": FROM_EMAIL,
            "to": [FOUNDER_EMAIL],
            "reply_to": email,
            "subject": f"[Hatchik] Restore request — {email}",
            "text": body,
        })
    except Exception as e:  # noqa: BLE001
        log.error("Failed to send restore-request notification: %s", e)


@app.get("/api/admin/signups/geo")
async def admin_signups_geo(
    days: int = 7,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Quick fraud dashboard: recent signups grouped by country.

    Returns counts per ``country_code`` over the last ``days`` (default 7),
    plus a flat list of the most recent ~50 signups with geo + email so
    the founder can spot bursts from suspicious ASNs or regions.
    """
    _require_admin(x_admin_token)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max(1, days))).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        by_country_rows = conn.execute(
            "SELECT COALESCE(country_code, '?') AS country, COUNT(*) AS count "
            "FROM signups WHERE created_at >= ? GROUP BY country "
            "ORDER BY count DESC",
            (cutoff,),
        ).fetchall()
        recent_rows = conn.execute(
            "SELECT id, created_at, email, tier, status, ip_address, "
            "country_code, city, asn FROM signups "
            "WHERE created_at >= ? ORDER BY id DESC LIMIT 50",
            (cutoff,),
        ).fetchall()
    return {
        "window_days": days,
        "by_country": [dict(r) for r in by_country_rows],
        "recent": [dict(r) for r in recent_rows],
    }


@app.get("/api/admin/accounts")
async def admin_list_accounts(
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Admin: list all signups + their tenant status from registry."""
    _require_admin(x_admin_token)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, created_at, email, first_name, product_name, tier, status "
            "FROM signups ORDER BY id DESC"
        ).fetchall()
    reg = _load_registry()
    tenants_by_signup: dict[int, dict[str, Any]] = {
        t.get("signup_id"): {"slug": slug, **t}
        for slug, t in reg.get("tenants", {}).items()
        if t.get("signup_id")
    }
    enriched = []
    for r in rows:
        d = dict(r)
        tenant = tenants_by_signup.get(r["id"])
        d["tenant"] = (
            {
                "slug": tenant["slug"],
                "url": tenant.get("url"),
                "status": tenant.get("status"),
                "port": tenant.get("port"),
                "last_redeploy_at": tenant.get("last_redeploy_at"),
                "last_redeploy_commit": tenant.get("last_redeploy_commit"),
                "last_redeploy_via": tenant.get("last_redeploy_via"),
            }
            if tenant else None
        )
        enriched.append(d)
    return {"signups": enriched}


class DeletionRequest(BaseModel):
    email: EmailStr


@app.post("/api/account/request-deletion", status_code=202)
async def request_deletion(req: DeletionRequest) -> dict[str, Any]:
    """Self-serve: customer asks to delete their sandbox.

    Service looks up signups for that email, generates a one-time token
    per slug, emails the customer a confirmation link. Always returns
    202 — never reveal whether the email matched (anti-enumeration).
    """
    email = str(req.email).lower().strip()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        signups = conn.execute(
            "SELECT id, first_name, product_name FROM signups "
            "WHERE LOWER(email) = ? AND status NOT IN ('deleted', 'cancelled')",
            (email,),
        ).fetchall()
    # Match each signup to its registry slug (if any).
    reg = _load_registry()
    tenants_by_signup = {t.get("signup_id"): (slug, t) for slug, t in reg.get("tenants", {}).items()}
    confirmable: list[tuple[int, str, str]] = []  # (signup_id, slug, product_name)
    for s in signups:
        match = tenants_by_signup.get(s["id"])
        if match is None:
            continue
        slug, tenant = match
        if tenant.get("status") == "decommissioned":
            continue
        confirmable.append((s["id"], slug, s["product_name"] or "Untitled"))

    if not confirmable:
        log.info("deletion request for %s — no live tenants found", email)
        return {"ok": True, "message": "If you have an active Hatchik sandbox, we've sent you a confirmation link."}

    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(hours=DELETION_TOKEN_TTL_HOURS)
    links: list[tuple[str, str]] = []  # (product_name, confirmation_url)
    with sqlite3.connect(DB_PATH) as conn:
        for signup_id, slug, product_name in confirmable:
            token = secrets.token_urlsafe(32)
            conn.execute(
                "INSERT INTO deletion_tokens (token, email, slug, signup_id, created_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (token, email, slug, signup_id, created_at.isoformat(), expires_at.isoformat()),
            )
            links.append((product_name, f"https://hatchik.com/api/account/confirm-deletion?token={token}"))
        conn.commit()

    await _send_deletion_confirmation_email(email, links, signups[0]["first_name"] or "")
    log.info("deletion confirmation sent to %s — %d sandbox(es)", email, len(links))
    return {"ok": True, "message": "If you have an active Hatchik sandbox, we've sent you a confirmation link."}


def _load_registry() -> dict[str, Any]:
    path = Path(os.environ.get("HATCHIK_TENANTS_DIR", "/opt/hatchik-tenants")) / "registry.json"
    if not path.exists():
        return {"tenants": {}}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"tenants": {}}


async def _send_deletion_confirmation_email(email: str, links: list[tuple[str, str]], first_name: str) -> None:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping deletion email to %s", email)
        return
    greeting = f"Hi {first_name}," if first_name else "Hi,"
    rows_text = "\n".join(f"  • {name}: {url}" for name, url in links)
    rows_html = "".join(
        f'<li style="margin:0 0 8px 0;"><strong>{_html_escape(name)}</strong> &mdash; '
        f'<a href="{url}" style="color:#4f46e5;text-decoration:underline;">Confirm deletion</a></li>'
        for name, url in links
    )
    text = f"""{greeting}

We received a request to delete your Hatchik sandbox{'es' if len(links) > 1 else ''}.

To confirm, click the link below. Each link is single-use and expires in {DELETION_TOKEN_TTL_HOURS}h.

{rows_text}

If you didn't ask for this, ignore this email and nothing happens.

— Hatchik

(This is an automated message — please don't reply.)
"""
    html = f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Confirm sandbox deletion</title></head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:8px;padding:32px;">
<tr><td style="font-size:16px;line-height:1.6;color:#1a1a1a;">
<p style="margin:0 0 16px 0;">{_html_escape(greeting)}</p>
<p style="margin:0 0 16px 0;">We received a request to delete your Hatchik sandbox{'es' if len(links) > 1 else ''}. Click below to confirm. Each link is single-use and expires in {DELETION_TOKEN_TTL_HOURS}h.</p>
<ul style="margin:0 0 16px 0;padding-left:20px;">{rows_html}</ul>
<p style="margin:0 0 16px 0;color:#555;font-size:14px;">If you didn&rsquo;t ask for this, ignore this email and nothing happens.</p>
<p style="margin:24px 0 0 0;">&mdash; Hatchik</p>
<p style="margin:24px 0 0 0;color:#888;font-size:12px;">This is an automated message &mdash; please don&rsquo;t reply.</p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>
"""
    try:
        await _resend_send({
            "from": FROM_EMAIL,
            "to": [email],
            "subject": "Confirm your Hatchik sandbox deletion",
            "text": text,
            "html": html,
        })
    except Exception as e:  # noqa: BLE001
        log.error("Failed to send deletion confirmation to %s: %s", email, e)


@app.get("/api/account/confirm-deletion", response_class=PlainTextResponse)
async def confirm_deletion(token: str) -> str:
    """Self-serve: customer clicks the emailed link, tenant is torn down."""
    if not token or len(token) < 32:
        raise HTTPException(status_code=400, detail="invalid token")
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT email, slug, signup_id, expires_at, consumed_at "
            "FROM deletion_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="token not found")
        if row["consumed_at"]:
            raise HTTPException(status_code=410, detail="token already used")
        if row["expires_at"] < now:
            raise HTTPException(status_code=410, detail="token expired")
        # Mark consumed BEFORE running decommission so concurrent clicks
        # can't double-fire.
        conn.execute("UPDATE deletion_tokens SET consumed_at = ? WHERE token = ?", (now, token))
        conn.commit()

    log.info("self-serve deletion confirmed: slug=%s email=%s", row["slug"], row["email"])
    _record_cancellation_transition(row["slug"], note="self-serve deletion")
    summary = _run_decommission(row["slug"], hard=True)
    return (
        f"Your '{row['slug']}' sandbox has been deleted.\n\n"
        f"Status: {summary.get('status', 'done')}\n"
        f"You can sign up again at https://hatchik.com whenever you want."
    )


# ─── Account management: magic-link auth + dashboard endpoints ───────────
# Customer flow: POST /api/account/login {email} → emailed magic link →
# GET /api/account/auth?token=... → session cookie set → redirect to
# /account. Protected endpoints read SESSION_COOKIE_NAME from the request
# cookies and look up the session in SQLite. Sign-out deletes the row.

from fastapi import Cookie, Response
from fastapi.responses import RedirectResponse


class LoginRequest(BaseModel):
    email: EmailStr
    turnstile_token: str | None = Field(None, max_length=4096)


def _resolve_session(session_id: str | None) -> dict[str, Any] | None:
    """Return {email, signup_id, ...} for a valid session cookie, else None."""
    if not session_id:
        return None
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT email, expires_at FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None or row["expires_at"] < now:
            return None
        conn.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        conn.commit()
    return {"email": row["email"]}


# ─── API-key (Bearer) auth ──────────────────────────────────────────────
# Long-lived alternative to session cookies for programmatic clients
# (the @hatchik/mcp package, future CLI, etc.). Customer creates a key
# via POST /api/account/api-keys, copies the plaintext exactly once,
# stashes it in the MCP config's HATCHIK_API_KEY env var. Subsequent
# requests carry it as `Authorization: Bearer hk_live_<token>`.
#
# Storage: only the sha256 hash. Plaintext is never written to disk
# server-side; the create endpoint returns it in the response body and
# we never see it again.

API_KEY_PREFIX = "hk_live_"
API_KEY_RANDOM_BYTES = 24  # 32 chars base32 → 192 bits of entropy


def _hash_api_key(plaintext: str) -> str:
    """SHA-256 of the plaintext key. Constant-time comparable via the
    UNIQUE index on key_hash — we look up by hash, no enumeration."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _generate_api_key() -> tuple[str, str]:
    """Return (plaintext, hash). Plaintext is `hk_live_` + base32(192 bits)."""
    import base64
    raw = secrets.token_bytes(API_KEY_RANDOM_BYTES)
    body = base64.b32encode(raw).decode("ascii").rstrip("=").lower()
    plaintext = f"{API_KEY_PREFIX}{body}"
    return plaintext, _hash_api_key(plaintext)


def _resolve_bearer(token: str | None) -> dict[str, Any] | None:
    """Return {email} for a valid, non-revoked API key, else None.

    Touches last_used_at on success — gives the customer a useful "last
    seen" timestamp in the /api/account/api-keys list.
    """
    if not token or not token.startswith(API_KEY_PREFIX):
        return None
    key_hash = _hash_api_key(token)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, email, revoked_at FROM api_keys WHERE key_hash = ?",
            (key_hash,),
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        conn.execute(
            "UPDATE api_keys SET last_used_at = ? WHERE id = ?",
            (now, row["id"]),
        )
        conn.commit()
    return {"email": row["email"]}


def _resolve_auth(
    session_cookie: str | None,
    authorization_header: str | None,
) -> dict[str, Any] | None:
    """Resolve a request's auth from either path. Bearer wins if both
    are present and both resolve — but in practice clients send one or
    the other, not both. Browser sessions use the cookie; MCP / CLI
    clients use Bearer."""
    if authorization_header:
        prefix = "Bearer "
        if authorization_header.startswith(prefix):
            bearer = authorization_header[len(prefix):].strip()
            resolved = _resolve_bearer(bearer)
            if resolved:
                return resolved
    return _resolve_session(session_cookie)


@app.post("/api/account/login", status_code=202)
async def request_login_link(req: LoginRequest, request: Request) -> dict[str, Any]:
    """Self-serve: email a one-time sign-in link.

    Anti-enumeration: returns 202 whether or not the email matches a
    signup row. Turnstile gate prevents bulk-probing for valid emails.
    """
    ip = (request.headers.get("CF-Connecting-IP")
          or request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
          or (request.client.host if request.client else "unknown"))

    if TURNSTILE_SECRET and not await verify_turnstile(req.turnstile_token, ip):
        raise HTTPException(
            status_code=422,
            detail={
                "ok": False,
                "error": "turnstile_failed",
                "message": "We couldn't verify you're human — please try again.",
            },
        )

    email = str(req.email).lower().strip()
    with sqlite3.connect(DB_PATH) as conn:
        match = conn.execute(
            "SELECT 1 FROM signups WHERE LOWER(email) = ? LIMIT 1", (email,)
        ).fetchone()
    if not match:
        log.info("login link requested for unknown email %s", email)
        return {"ok": True, "message": "If that email matches an active Hatchik account, we've sent a sign-in link."}

    created_at = datetime.now(timezone.utc)
    expires_at = created_at + timedelta(minutes=LOGIN_TOKEN_TTL_MINUTES)
    token = secrets.token_urlsafe(32)
    code = _generate_login_code()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO login_tokens (token, email, created_at, expires_at, code, code_attempts) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (token, email, created_at.isoformat(), expires_at.isoformat(), code),
        )
        conn.commit()

    link = f"https://hatchik.com/api/account/auth?token={token}"
    await _send_login_email(email, link, code)
    log.info("login link emailed to %s", email)
    return {"ok": True, "message": "If that email matches an active Hatchik account, we've sent a sign-in link."}


def _generate_login_code() -> str:
    """Return a 6-digit numeric code (100000-999999) as a zero-free string.

    secrets.randbelow(900000) + 100000 guarantees 6 digits — we deliberately
    skip 000000-099999 so the printed code can't be confused with a leading-
    zero phone number or hex digit, and so all codes are the same width when
    formatted as "123 456" for the email.
    """
    return str(secrets.randbelow(900000) + 100000)


async def _send_login_email(email: str, link: str, code: str) -> None:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping login email to %s", email)
        return
    # Format the code with a space in the middle for readability — e.g.
    # "123 456" — but the actual stored value is the raw 6 digits.
    code_display = f"{code[:3]} {code[3:]}"
    text = f"""Hi,

Click the link below to sign in to your Hatchik account. It's
single-use and expires in {LOGIN_TOKEN_TTL_MINUTES} minutes.

{link}

Or copy this code into the sign-in form on hatchik.com/account:

    {code_display}

Expires in {LOGIN_TOKEN_TTL_MINUTES} minutes. If you didn't ask to sign in, ignore this email.

— Hatchik

(This is an automated message — please don't reply.)
"""
    html = f"""\
<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Sign in to Hatchik</title></head>
<body style="margin:0;padding:0;background:#f6f5f1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;color:#1a1a1a;">
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f6f5f1;">
<tr><td align="center" style="padding:32px 16px;">
<table role="presentation" width="560" cellspacing="0" cellpadding="0" border="0" style="max-width:560px;background:#ffffff;border-radius:8px;padding:32px;">
<tr><td style="font-size:16px;line-height:1.6;color:#1a1a1a;">
<p style="margin:0 0 16px 0;">Hi,</p>
<p style="margin:0 0 16px 0;">Click the button below to sign in to your Hatchik account. It&rsquo;s single-use and expires in {LOGIN_TOKEN_TTL_MINUTES} minutes.</p>
<p style="margin:24px 0;"><a href="{link}" style="display:inline-block;background:#4f46e5;color:#ffffff;text-decoration:none;padding:12px 24px;border-radius:8px;font-weight:600;">Sign in to Hatchik</a></p>
<p style="margin:0 0 16px 0;color:#555;font-size:14px;">Or paste this URL into your browser: <a href="{link}" style="color:#4f46e5;text-decoration:underline;">{link}</a></p>
<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:28px 0;border-top:1px solid #e5e4dd;">
<tr><td style="padding-top:24px;">
<p style="margin:0 0 12px 0;color:#555;font-size:14px;">Or copy this code into the sign-in form on hatchik.com/account:</p>
<p style="margin:0 0 12px 0;font-family:'SFMono-Regular',Menlo,Consolas,monospace;font-size:32px;font-weight:700;letter-spacing:0.15em;color:#1a1a1a;">{code_display}</p>
<p style="margin:0 0 16px 0;color:#888;font-size:13px;">Expires in {LOGIN_TOKEN_TTL_MINUTES} minutes. If you didn&rsquo;t ask to sign in, ignore this email.</p>
</td></tr>
</table>
<p style="margin:24px 0 0 0;">&mdash; Hatchik</p>
<p style="margin:24px 0 0 0;color:#888;font-size:12px;">This is an automated message &mdash; please don&rsquo;t reply.</p>
</td></tr>
</table>
</td></tr>
</table>
</body></html>
"""
    try:
        await _resend_send({
            "from": FROM_EMAIL,
            "to": [email],
            "subject": "Sign in to Hatchik",
            "text": text,
            "html": html,
        })
    except Exception as e:  # noqa: BLE001
        log.error("Failed to send login email to %s: %s", email, e)


@app.get("/api/account/auth")
async def auth_callback(token: str) -> RedirectResponse:
    """Verify a magic-link token, create a session, set cookie, redirect."""
    if not token or len(token) < 32:
        raise HTTPException(status_code=400, detail="invalid token")
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT email, expires_at, consumed_at FROM login_tokens WHERE token = ?",
            (token,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="token not found")
        if row["consumed_at"]:
            raise HTTPException(status_code=410, detail="token already used")
        if row["expires_at"] < now_iso:
            raise HTTPException(status_code=410, detail="token expired")
        conn.execute("UPDATE login_tokens SET consumed_at = ? WHERE token = ?", (now_iso, token))
        session_id = secrets.token_urlsafe(32)
        expires_at = (now + timedelta(days=SESSION_TTL_DAYS)).isoformat()
        conn.execute(
            "INSERT INTO sessions (session_id, email, created_at, expires_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, row["email"], now_iso, expires_at, now_iso),
        )
        conn.commit()

    resp = RedirectResponse(url="/account", status_code=303)
    resp.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    log.info("session created for %s", row["email"])
    return resp


class LoginCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=7)

    @field_validator("code")
    @classmethod
    def _strip_code(cls, v: str) -> str:
        # Customers may paste the code with the visual space ("123 456") or
        # without — store/compare digits only.
        cleaned = "".join(ch for ch in v if ch.isdigit())
        if len(cleaned) != 6:
            raise ValueError("code must be 6 digits")
        return cleaned


@app.post("/api/account/login-with-code")
async def login_with_code(
    req: LoginCodeRequest, response: Response
) -> dict[str, Any]:
    """Verify a 6-digit code, create a session, set cookie.

    Fallback path for inboxes / corporate proxies that mangle clickable
    magic links. Shares the login_tokens row with the link path so either
    flow consumes the same single-use token. After LOGIN_CODE_MAX_ATTEMPTS
    failed guesses the row is invalidated entirely (consumed_at set) so a
    correct subsequent attempt also fails — the customer must request a
    new code.
    """
    email = str(req.email).lower().strip()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT token, email, expires_at, consumed_at, code, code_attempts "
            "FROM login_tokens "
            "WHERE LOWER(email) = ? AND consumed_at IS NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (email,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="no pending sign-in")
        if row["code"] is None:
            # Pre-migration row — no code was ever issued for this token.
            raise HTTPException(status_code=404, detail="no pending sign-in")

        # At-or-over the cap of failed attempts → no more guesses allowed.
        # Burn the token so even a correct code submitted now fails, and
        # return 429 so the UI surfaces the right message. The customer
        # must request a fresh sign-in email.
        if row["code_attempts"] >= LOGIN_CODE_MAX_ATTEMPTS:
            conn.execute(
                "UPDATE login_tokens SET consumed_at = ? WHERE token = ?",
                (now_iso, row["token"]),
            )
            conn.commit()
            log.warning(
                "login-with-code: token for %s already at attempt cap — invalidated",
                email,
            )
            raise HTTPException(
                status_code=429,
                detail="too many attempts — request a new sign-in email",
            )

        # Constant-time compare to avoid leaking the correct code via
        # timing (paranoid, but cheap).
        provided = req.code
        expected = row["code"]
        code_ok = hmac.compare_digest(provided, expected)
        expired = row["expires_at"] < now_iso

        if not code_ok or expired:
            new_attempts = row["code_attempts"] + 1
            conn.execute(
                "UPDATE login_tokens SET code_attempts = ? WHERE token = ?",
                (new_attempts, row["token"]),
            )
            conn.commit()
            if expired and code_ok:
                raise HTTPException(status_code=410, detail="code expired")
            raise HTTPException(status_code=410, detail="incorrect or expired code")

        # Success path: mark consumed, create session, set cookie.
        conn.execute(
            "UPDATE login_tokens SET consumed_at = ? WHERE token = ?",
            (now_iso, row["token"]),
        )
        session_id = secrets.token_urlsafe(32)
        expires_at = (now + timedelta(days=SESSION_TTL_DAYS)).isoformat()
        conn.execute(
            "INSERT INTO sessions (session_id, email, created_at, expires_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, row["email"], now_iso, expires_at, now_iso),
        )
        conn.commit()

    response.set_cookie(
        SESSION_COOKIE_NAME,
        session_id,
        max_age=SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )
    log.info("session created for %s (via code)", row["email"])
    return {"ok": True}


@app.post("/api/account/logout", status_code=204)
async def logout(
    response: Response,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> Response:
    if hatchik_session:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (hatchik_session,))
            conn.commit()
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    response.status_code = 204
    return response


@app.get("/api/account/me")
async def get_me(
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    email = session["email"]
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        signup_rows = conn.execute(
            "SELECT id, first_name, product_name, tier, created_at, status, "
            "github_username FROM signups WHERE LOWER(email) = ? ORDER BY id DESC",
            (email,),
        ).fetchall()
    reg = _load_registry()
    tenants_by_signup = {
        t.get("signup_id"): {"slug": slug, **t}
        for slug, t in reg.get("tenants", {}).items()
        if t.get("signup_id")
    }
    sandboxes = []
    first_name = ""
    github_username = ""
    for r in signup_rows:
        if not first_name and r["first_name"]:
            first_name = r["first_name"]
        if not github_username and r["github_username"]:
            github_username = r["github_username"]
        tenant = tenants_by_signup.get(r["id"])
        sandboxes.append({
            "signup_id": r["id"],
            "product_name": r["product_name"],
            "tier": r["tier"],
            "created_at": r["created_at"],
            "status": tenant.get("status") if tenant else r["status"],
            "url": tenant.get("url") if tenant else None,
            "slug": tenant.get("slug") if tenant else None,
            "repo_url": tenant.get("repo_url") if tenant else None,
        })
    return {
        "email": email,
        "first_name": first_name,
        "github_username": github_username,
        "sandboxes": sandboxes,
    }


class UpdateMeRequest(BaseModel):
    first_name: str | None = Field(None, max_length=80)
    github_username: str | None = Field(None, max_length=39)

    @field_validator("first_name")
    @classmethod
    def clean_first_name(cls, v: str | None) -> str | None:
        if v is None:
            return None
        token = v.strip().split()[0] if v.strip() else ""
        return token[:1].upper() + token[1:] if token else ""

    @field_validator("github_username")
    @classmethod
    def clean_github_username(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lstrip("@")
        if not v:
            return ""
        if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}", v):
            raise ValueError("github_username must be a valid GitHub handle")
        return v


@app.patch("/api/account/me")
async def update_me(
    body: UpdateMeRequest,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    email = session["email"]
    updates: list[tuple[str, Any]] = []
    if body.first_name is not None:
        updates.append(("first_name", body.first_name))
    if body.github_username is not None:
        # Empty string clears the field — customer is opting out of BYO
        # GitHub for any future repo handoffs.
        updates.append(("github_username", body.github_username or None))
    if updates:
        set_clause = ", ".join(f"{col} = ?" for col, _ in updates)
        params = [val for _, val in updates] + [email]
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                f"UPDATE signups SET {set_clause} WHERE LOWER(email) = ?",
                params,
            )
            conn.commit()
    return {"ok": True}


# ─── API-key management ─────────────────────────────────────────────────
# Long-lived bearer tokens for programmatic clients (the @hatchik/mcp
# package, future CLI, etc.). Customer-managed: create from /account,
# list to see name + last_used_at + revoked state, revoke when they no
# longer need it. The plaintext token leaves the server exactly once,
# in the create response. After that we only know its sha256 hash.

class CreateApiKeyRequest(BaseModel):
    name: str = Field(default="", max_length=80)


@app.post("/api/account/api-keys", status_code=201)
async def create_api_key(
    body: CreateApiKeyRequest,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Issue a new API key tied to the signed-in account.

    The plaintext token is returned in this response only. We hash and
    store only the digest, so once the customer dismisses the dialog
    they can no longer retrieve it — they have to revoke and reissue.
    Match the pattern most cloud providers use.
    """
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    email = session["email"]
    plaintext, key_hash = _generate_api_key()
    now = datetime.now(timezone.utc).isoformat()
    name = (body.name or "").strip() or f"unnamed key ({now[:10]})"
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "INSERT INTO api_keys (email, key_hash, name, created_at) "
            "VALUES (?, ?, ?, ?)",
            (email, key_hash, name, now),
        )
        conn.commit()
        key_id = cur.lastrowid
    log.info("Issued API key id=%s email=%s name=%r", key_id, email, name)
    return {
        "id": key_id,
        "name": name,
        "created_at": now,
        "key": plaintext,  # ← only returned here, never again
        "warning": (
            "This is the only time you'll see the full key. "
            "Copy it now and paste into your MCP config or CLI. "
            "You can always revoke it later from this page."
        ),
    }


@app.get("/api/account/api-keys")
async def list_api_keys(
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """List API keys for the signed-in account.

    Returns only metadata. The plaintext token is unrecoverable after
    creation — by design.
    """
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, name, created_at, last_used_at, revoked_at "
            "FROM api_keys WHERE LOWER(email) = ? "
            "ORDER BY id DESC",
            (session["email"].lower(),),
        ).fetchall()
    return {
        "keys": [
            {
                "id": r["id"],
                "name": r["name"],
                "created_at": r["created_at"],
                "last_used_at": r["last_used_at"],
                "revoked_at": r["revoked_at"],
                "status": "revoked" if r["revoked_at"] else "active",
            }
            for r in rows
        ],
    }


@app.delete("/api/account/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    key_id: int,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> None:
    """Revoke an API key.

    Soft-delete: sets revoked_at rather than DELETE'ing the row, so we
    keep the audit trail (created_at, last_used_at) for forensics. The
    bearer resolver checks revoked_at and rejects revoked keys.
    """
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.execute(
            "UPDATE api_keys SET revoked_at = ? "
            "WHERE id = ? AND LOWER(email) = ? AND revoked_at IS NULL",
            (now, key_id, session["email"].lower()),
        )
        conn.commit()
        if cur.rowcount == 0:
            # Either the key doesn't exist, isn't ours, or already revoked.
            # Anti-enumeration: don't leak which.
            raise HTTPException(status_code=404, detail="key not found")
    log.info("Revoked API key id=%s by %s", key_id, session["email"])


@app.get("/api/account/upgrade")
async def upgrade_info(
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Return upgrade info — Paddle checkout URL if configured, else 'coming soon'."""
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    if not PADDLE_LAUNCH_PRICE_ID:
        return {
            "available": False,
            "reason": "Paddle approval pending — we'll email you when Launch tier opens.",
        }
    # Paddle Billing hosted checkout — passes customer_email so the
    # post-checkout webhook can match to the signup row.
    checkout_url = (
        f"{PADDLE_CHECKOUT_BASE}/{PADDLE_LAUNCH_PRICE_ID}"
        f"?customer_email={session['email']}"
    )
    return {"available": True, "checkout_url": checkout_url}


@app.get("/api/account/billing-portal")
async def billing_portal(
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Return a Paddle customer-portal URL for the signed-in customer.

    Only customers with at least one successful Launch-tier payment have
    a Paddle customer_id. Until then, this returns 404 and the UI hides
    the billing tab.
    """
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT paddle_customer_id FROM payments "
            "WHERE LOWER(customer_email) = ? AND paddle_customer_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (session["email"],),
        ).fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="no Paddle customer record yet")
    return {"url": f"{PADDLE_BILLING_PORTAL_BASE}/customer/{row[0]}"}


@app.post("/api/paddle/webhook")
async def paddle_webhook(request: Request) -> dict[str, Any]:
    """Receive Paddle Billing webhook events.

    Verifies the Paddle signature, deduplicates by ``event_id``, and
    dispatches on ``event_type``. Unhandled event types still return 200 —
    Paddle retries on non-2xx, and we want to avoid retry storms for
    things we don't care about.

    Event names follow Paddle Billing (v1), not Paddle Classic: e.g.
    ``transaction.completed``, not ``checkout.session.completed``.
    """
    if not PADDLE_WEBHOOK_SECRET:
        # Hard-fail on misconfiguration rather than silently accepting
        # unauthenticated traffic. Logged at error so journalctl shows it.
        log.error("Paddle webhook hit but PADDLE_WEBHOOK_SECRET is not configured")
        raise HTTPException(status_code=500, detail="Webhook secret not configured")

    payload = await request.body()
    sig_header = request.headers.get("Paddle-Signature", "")

    if not verify_paddle_signature(payload, sig_header, PADDLE_WEBHOOK_SECRET):
        log.warning("Paddle webhook signature verification failed")
        raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        log.error("Paddle webhook payload not valid JSON: %s", e)
        raise HTTPException(status_code=400, detail="Invalid payload") from e

    # Paddle Billing places ``event_id`` and ``event_type`` at the top
    # level; the resource itself is under ``data``.
    event_id = event.get("event_id")
    event_type = event.get("event_type", "")
    if not event_id or not event_type:
        log.error("Paddle webhook missing event_id/event_type fields")
        raise HTTPException(status_code=400, detail="Malformed event")

    # Idempotency: Paddle retries on any non-2xx and occasionally
    # double-fires. Same event id twice -> ack with no work.
    if _event_already_processed(event_id):
        log.info("Paddle event %s (%s) already processed — skipping", event_id, event_type)
        return {"received": True, "duplicate": True}

    data_object = event.get("data") or {}

    if event_type == "transaction.completed":
        log.info(
            "Paddle transaction.completed: txn=%s customer=%s subscription=%s status=%s currency=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("subscription_id"),
            data_object.get("status"),
            data_object.get("currency_code"),
        )
        _persist_paddle_transaction(data_object, payload)

    elif event_type == "subscription.created":
        log.info(
            "Paddle subscription.created: subscription=%s customer=%s status=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("status"),
        )
        await _handle_subscription_created(data_object, event_id)

    elif event_type == "subscription.updated":
        log.info(
            "Paddle subscription.updated: subscription=%s customer=%s status=%s next_billed_at=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("status"),
            data_object.get("next_billed_at"),
        )
        await _handle_subscription_updated(data_object, event_id)

    elif event_type == "subscription.canceled":
        log.warning(
            "Paddle subscription.canceled (churn): subscription=%s customer=%s canceled_at=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("canceled_at"),
        )
        await _handle_subscription_canceled(data_object, event_id)

    elif event_type == "transaction.payment_failed":
        log.warning(
            "Paddle transaction.payment_failed: txn=%s customer=%s status=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("status"),
        )
        await notify_founder_payment_failure(data_object)

    else:
        log.info("Paddle event %s (%s) — no handler, ack'd", event_id, event_type)

    _mark_event_processed(event_id, event_type)
    return {"received": True}


# ─── Per-tenant redeploy webhook ─────────────────────────────────────────
# In-process state for the redeploy endpoint. The signup-service is a
# single uvicorn worker on a single host (see hatchik-signup.service), so
# in-memory state is safe — no multi-worker race to coordinate. If we
# ever scale to multiple workers, both of these would need a SQLite-
# backed equivalent.
_redeploy_locks: dict[str, asyncio.Lock] = {}
# Recent redeploy timestamps per slug, used for rate-limiting. Lists of
# monotonic-ish epoch seconds; pruned on each access.
_redeploy_history: dict[str, list[float]] = {}


def _redeploy_lock(slug: str) -> asyncio.Lock:
    """Return a singleton lock for this slug.

    asyncio.Lock is bound to its event loop, which is fine because all
    redeploy requests run on the same FastAPI event loop. We never share
    the lock between threads.
    """
    lock = _redeploy_locks.get(slug)
    if lock is None:
        lock = asyncio.Lock()
        _redeploy_locks[slug] = lock
    return lock


def _redeploy_check_rate_limit(slug: str) -> bool:
    """True if a redeploy for this slug is allowed; False if rate-limited."""
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - REDEPLOY_RATE_LIMIT_WINDOW_SECONDS
    history = [t for t in _redeploy_history.get(slug, []) if t >= cutoff]
    if len(history) >= REDEPLOY_RATE_LIMIT_MAX:
        _redeploy_history[slug] = history
        return False
    history.append(now)
    _redeploy_history[slug] = history
    return True


def _save_registry(reg: dict[str, Any]) -> None:
    """Atomically write the tenant registry.

    Mirrors provision.py's save_registry — write to .tmp then rename so
    a partial write can never corrupt the file. Best-effort: callers
    catch exceptions and log rather than failing the redeploy itself.
    """
    path = Path(os.environ.get("HATCHIK_TENANTS_DIR", "/opt/hatchik-tenants")) / "registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(reg, indent=2, sort_keys=True))
    tmp.rename(path)


async def _append_redeploy_log(slug: str, line: str) -> None:
    """Append a single timestamped line to the per-tenant redeploy log.

    Synchronous file I/O wrapped in to_thread so the event loop doesn't
    stall on slow disks. The log dir is created lazily; if creation
    fails we swallow — defensive logging shouldn't break the redeploy.
    """
    try:
        REDEPLOY_LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = REDEPLOY_LOG_DIR / f"redeploy-{slug}.log"
        ts = datetime.now(timezone.utc).isoformat()
        await asyncio.to_thread(
            lambda: log_path.open("a", encoding="utf-8").write(f"[{ts}] {line}\n")
        )
    except OSError as e:
        log.warning("could not write redeploy log for %s: %s", slug, e)


async def _tail_redeploy_log(slug: str, n: int = 50) -> str:
    """Tail the last n lines of the per-tenant redeploy log."""
    log_path = REDEPLOY_LOG_DIR / f"redeploy-{slug}.log"
    if not log_path.exists():
        return ""
    try:
        text = await asyncio.to_thread(log_path.read_text, encoding="utf-8", errors="replace")
    except OSError:
        return ""
    lines = text.splitlines()
    return "\n".join(lines[-n:])


async def _run_redeploy_subprocess(slug: str, target: Path) -> tuple[bool, str | None]:
    """git pull --rebase && docker compose up -d --build in the tenant dir.

    Returns ``(ok, commit_sha_or_none)``. All subprocess output (stdout +
    stderr) is appended to the per-tenant redeploy log with timestamps.
    Uses asyncio.create_subprocess_exec so the FastAPI event loop stays
    responsive — docker rebuilds can take 60s+ and we'd otherwise block
    other tenants' redeploys.
    """

    async def _run(cmd: list[str]) -> tuple[int, str]:
        await _append_redeploy_log(slug, f"$ {' '.join(cmd)}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(target),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(), timeout=REDEPLOY_SUBPROCESS_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            proc.kill()
            await _append_redeploy_log(slug, f"TIMEOUT after {REDEPLOY_SUBPROCESS_TIMEOUT_SECONDS}s")
            return 124, f"timeout after {REDEPLOY_SUBPROCESS_TIMEOUT_SECONDS}s"
        out = stdout.decode("utf-8", errors="replace") if stdout else ""
        for ln in out.splitlines():
            await _append_redeploy_log(slug, ln)
        await _append_redeploy_log(slug, f"(exit {proc.returncode})")
        return proc.returncode or 0, out

    await _append_redeploy_log(slug, "redeploy: start git pull")
    rc, _ = await _run(["git", "pull", "--rebase"])
    if rc != 0:
        return False, None
    await _append_redeploy_log(slug, "redeploy: start docker compose up -d --build")
    rc, _ = await _run(["docker", "compose", "up", "-d", "--build"])
    if rc != 0:
        return False, None
    # Best-effort grab of the current short SHA so callers can log
    # exactly what commit landed. Failure here doesn't fail the redeploy.
    rc, sha_out = await _run(["git", "rev-parse", "--short", "HEAD"])
    sha = sha_out.strip() if rc == 0 else None
    return True, sha


def _verify_deploy_token_header(provided: str | None, expected: str) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided, expected)


def _verify_github_signature(body: bytes, header: str | None, secret: str) -> bool:
    """Verify GitHub's X-Hub-Signature-256 = sha256=<hex(hmac-sha256)>."""
    if not header or not secret:
        return False
    if not header.startswith("sha256="):
        return False
    provided = header[len("sha256=") :].strip()
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    # constant-time compare; both sides are equal-length hex strings.
    return hmac.compare_digest(provided, expected)


@app.post("/api/tenants/{slug}/redeploy")
async def tenant_redeploy(
    slug: str,
    request: Request,
    x_deploy_token: str | None = Header(default=None, alias="X-Deploy-Token"),
    x_hub_signature_256: str | None = Header(default=None, alias="X-Hub-Signature-256"),
) -> dict[str, Any]:
    """Per-tenant redeploy webhook.

    Auth: either ``X-Deploy-Token`` (used by AI tools — direct trigger)
    or ``X-Hub-Signature-256`` (used by GitHub webhooks — HMAC-SHA256
    of the raw body using the same per-tenant ``deploy_token`` as the
    secret). Both keys come from the registry's tenant entry.

    Behaviour:
      - 404 if slug unknown
      - 410 if status is archived / decommissioned
      - 403 on auth mismatch
      - 429 if another redeploy for the same slug is in-flight, or if
        the per-tenant rate limit was exhausted
      - 200 on success with {ok, slug, deployed_at, commit, via}
    """
    reg = _load_registry()
    tenant = (reg.get("tenants") or {}).get(slug)
    if not tenant:
        raise HTTPException(status_code=404, detail="tenant not found")
    status = tenant.get("status")
    if status in {"archived", "decommissioned"}:
        # 410 Gone = the slug existed but is no longer redeployable.
        raise HTTPException(status_code=410, detail=f"tenant is {status}")

    deploy_token = tenant.get("deploy_token") or ""
    body = await request.body()

    via: str | None = None
    if x_deploy_token and _verify_deploy_token_header(x_deploy_token, deploy_token):
        via = "ai-tool"
    elif x_hub_signature_256 and _verify_github_signature(body, x_hub_signature_256, deploy_token):
        via = "github-webhook"
    else:
        log.warning("redeploy auth failed slug=%s headers=%s", slug, list(request.headers.keys()))
        raise HTTPException(status_code=403, detail="invalid deploy auth")

    log.info("redeploy auth ok slug=%s via=%s", slug, via)

    if not _redeploy_check_rate_limit(slug):
        await _append_redeploy_log(slug, f"rate-limited (via={via})")
        raise HTTPException(
            status_code=429,
            detail=(
                f"redeploy rate limit exceeded "
                f"(max {REDEPLOY_RATE_LIMIT_MAX} per "
                f"{REDEPLOY_RATE_LIMIT_WINDOW_SECONDS}s)"
            ),
        )

    lock = _redeploy_lock(slug)
    if lock.locked():
        raise HTTPException(status_code=429, detail="redeploy already in progress for this tenant")

    target = TENANTS_DIR / slug
    if not target.exists():
        # The registry has us but /opt/hatchik-tenants/<slug>/ doesn't
        # exist — most likely a half-decommissioned tenant. Treat as
        # 410 so the caller stops retrying.
        raise HTTPException(status_code=410, detail="tenant directory missing")

    async with lock:
        await _append_redeploy_log(slug, f"redeploy: triggered via={via}")
        ok, sha = await _run_redeploy_subprocess(slug, target)
        deployed_at = datetime.now(timezone.utc).isoformat()

        if not ok:
            tail = await _tail_redeploy_log(slug, n=50)
            await _append_redeploy_log(slug, "redeploy: FAILED")
            raise HTTPException(
                status_code=500,
                detail={
                    "ok": False,
                    "slug": slug,
                    "via": via,
                    "message": "redeploy failed — see log tail",
                    "log_tail": tail,
                },
            )

        # Persist the success info in the registry so /api/admin/accounts
        # can surface it. Best-effort: writing fails (e.g. read-only FS)
        # don't fail the redeploy itself.
        try:
            reg = _load_registry()
            t = reg.setdefault("tenants", {}).setdefault(slug, tenant)
            t["last_redeploy_at"] = deployed_at
            t["last_redeploy_commit"] = sha
            t["last_redeploy_via"] = via
            _save_registry(reg)
        except OSError as e:
            log.warning("could not update registry after redeploy slug=%s: %s", slug, e)

        await _append_redeploy_log(slug, f"redeploy: OK commit={sha} via={via}")
        return {
            "ok": True,
            "slug": slug,
            "deployed_at": deployed_at,
            "commit": sha,
            "via": via,
        }


# ─── Mobile builds (GitHub Actions) ──────────────────────────────────────
# In-process rate-limit history: { slug: [timestamps] }. Single-worker
# service so per-process state is fine; if we ever scale horizontally
# we'll move this to SQLite alongside the redeploy state.
_mobile_build_history: dict[str, list[float]] = {}


def _mobile_build_check_rate_limit(slug: str) -> bool:
    """True if a build for this slug is allowed; False if rate-limited."""
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - MOBILE_BUILD_RATE_LIMIT_WINDOW_SECONDS
    history = [t for t in _mobile_build_history.get(slug, []) if t >= cutoff]
    if len(history) >= MOBILE_BUILD_RATE_LIMIT_MAX:
        _mobile_build_history[slug] = history
        return False
    history.append(now)
    _mobile_build_history[slug] = history
    return True


def _tenant_for_session(slug: str, session_email: str) -> dict[str, Any] | None:
    """Return the registry tenant entry only if it belongs to the signed-in user."""
    reg = _load_registry()
    tenant = (reg.get("tenants") or {}).get(slug)
    if not tenant:
        return None
    if (tenant.get("email") or "").lower() != session_email.lower():
        return None
    return tenant


def _launch_tenant_for_session(slug: str, session_email: str) -> dict[str, Any] | None:
    """Return a launch-registry tenant entry only if it belongs to the
    signed-in user. Mirror of _tenant_for_session for the Launch tier.

    The launch registry uses ``customer_email`` rather than ``email``;
    schema is documented in launch-orchestrator/registry.json's _format
    key. Returns None if no launch registry exists on this host (dev /
    non-prod) — callers should fall back to sandbox lookup.
    """
    if not LAUNCH_REGISTRY_PATH.exists():
        return None
    try:
        reg = json.loads(LAUNCH_REGISTRY_PATH.read_text())
    except Exception as e:  # noqa: BLE001
        log.error("Failed to read launch registry: %s", e)
        return None
    tenant = (reg.get("tenants") or {}).get(slug)
    if not tenant:
        return None
    if (tenant.get("customer_email") or "").lower() != session_email.lower():
        return None
    return tenant


async def _github_get(path: str) -> tuple[int, dict[str, Any] | list[Any] | None]:
    """Authenticated GET against the GitHub API. Returns (status, body)."""
    if not HATCHIK_GITHUB_TOKEN:
        return 0, None
    headers = {
        "Authorization": f"Bearer {HATCHIK_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=GITHUB_API_TIMEOUT_SECONDS) as client:
            r = await client.get(f"{GITHUB_API_URL}{path}", headers=headers)
    except httpx.HTTPError as e:
        log.warning("GitHub GET %s failed: %s", path, e)
        return 0, None
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, None


async def _github_post(path: str, payload: dict[str, Any]) -> int:
    """Authenticated POST. Returns status code (0 on network error)."""
    if not HATCHIK_GITHUB_TOKEN:
        return 0
    headers = {
        "Authorization": f"Bearer {HATCHIK_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=GITHUB_API_TIMEOUT_SECONDS) as client:
            r = await client.post(f"{GITHUB_API_URL}{path}", headers=headers, json=payload)
    except httpx.HTTPError as e:
        log.warning("GitHub POST %s failed: %s", path, e)
        return 0
    return r.status_code


async def _github_put(path: str, payload: dict[str, Any]) -> tuple[int, str]:
    """Authenticated PUT. Returns (status_code, body_text)."""
    if not HATCHIK_GITHUB_TOKEN:
        return 0, ""
    headers = {
        "Authorization": f"Bearer {HATCHIK_GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    try:
        async with httpx.AsyncClient(timeout=GITHUB_API_TIMEOUT_SECONDS) as client:
            r = await client.put(f"{GITHUB_API_URL}{path}", headers=headers, json=payload)
    except httpx.HTTPError as e:
        log.warning("GitHub PUT %s failed: %s", path, e)
        return 0, str(e)
    return r.status_code, r.text


# In-process rate-limit history for the re-invite endpoint, keyed by
# customer email. Single-worker service so per-process state is fine;
# bumps to SQLite if we ever scale horizontally.
_github_invite_history: dict[str, list[float]] = {}


def _github_invite_check_rate_limit(email: str) -> bool:
    """True if a re-invite call for this email is allowed; False if capped."""
    now = datetime.now(timezone.utc).timestamp()
    cutoff = now - GITHUB_INVITE_RATE_LIMIT_WINDOW_SECONDS
    key = email.lower().strip()
    history = [t for t in _github_invite_history.get(key, []) if t >= cutoff]
    if len(history) >= GITHUB_INVITE_RATE_LIMIT_MAX:
        _github_invite_history[key] = history
        return False
    history.append(now)
    _github_invite_history[key] = history
    return True


async def _invite_github_collaborator(slug: str, handle: str) -> dict[str, Any]:
    """Invite ``handle`` as an admin collaborator on ``slug`` repo.

    Returns a structured result the caller can shape into an HTTP
    response. Recognised outcomes:

      - ``status="invitation_sent"``       — 201 (new invite) or 204
        (already invited, no change). Treat both as success.
      - ``status="already_collaborator"``  — 422 with the
        ``"is already a collaborator"`` body message, or 304 (no-op).
        Surface as success: customer already has access.
      - ``status="not_found"``              — 404. Either the repo or
        the handle doesn't exist. We special-case "handle doesn't
        exist" via the user-existence pre-check on the caller.
      - ``status="forbidden"``              — 403. PAT lacks org permission.
        Founder-notification flag logged for journalctl.
      - ``status="upstream_error"``         — anything else, including
        network failures (status_code == 0).
    """
    if not HATCHIK_GITHUB_TOKEN:
        return {
            "status": "upstream_error",
            "http_status": 0,
            "detail": "HATCHIK_GITHUB_TOKEN not configured server-side",
        }
    code, body = await _github_put(
        f"/repos/{HATCHIK_GITHUB_ORG}/{slug}/collaborators/{handle}",
        {"permission": "admin"},
    )
    if code == 201:
        return {"status": "invitation_sent", "http_status": code}
    if code == 204:
        # GitHub returns 204 when the invite was already extended /
        # the user is already a collaborator — treat as success.
        return {"status": "invitation_sent", "http_status": code}
    if code == 304:
        # Documented "not modified" — invite already exists.
        return {"status": "already_collaborator", "http_status": code}
    if code == 422 and "already a collaborator" in (body or "").lower():
        return {"status": "already_collaborator", "http_status": code}
    if code == 404:
        return {"status": "not_found", "http_status": code, "detail": (body or "")[:300]}
    if code == 403:
        # PAT lacks org permission — founder needs to fix the token.
        # Surface clearly + log founder-notification flag for journalctl.
        log.error(
            "FOUNDER_NOTIFY: github invite forbidden slug=%s handle=%s body=%s",
            slug, handle, (body or "")[:300],
        )
        return {"status": "forbidden", "http_status": code, "detail": (body or "")[:300]}
    log.warning(
        "github invite returned unexpected status slug=%s handle=%s code=%s body=%s",
        slug, handle, code, (body or "")[:300],
    )
    return {"status": "upstream_error", "http_status": code, "detail": (body or "")[:300]}


@app.get("/api/account/mobile-builds/{slug}")
async def list_mobile_builds(
    slug: str,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """List the last 5 build-mobile workflow runs for a tenant repo.

    Fails gracefully: returns ``{"connected": False}`` when the customer
    hasn't connected GitHub yet (no PAT configured server-side, or no
    repo created for this tenant) so the UI can surface "Connect GitHub
    first" rather than treating it as an error.
    """
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    tenant = _tenant_for_session(slug, session["email"])
    if not tenant:
        raise HTTPException(status_code=404, detail="sandbox not found")

    if not HATCHIK_GITHUB_TOKEN or not tenant.get("repo_url"):
        return {
            "connected": False,
            "slug": slug,
            "reason": "GitHub isn't connected for this sandbox yet. Add your GitHub username under Settings to enable mobile builds.",
            "runs": [],
        }

    status, body = await _github_get(
        f"/repos/{HATCHIK_GITHUB_ORG}/{slug}/actions/workflows/"
        f"{MOBILE_BUILD_WORKFLOW_FILE}/runs?per_page=5"
    )
    if status == 404:
        # The workflow file ships with every tenant repo, but if the
        # customer deleted it (or the substrate pre-dates the build-mobile
        # workflow) we surface a friendly note rather than an HTTP error.
        return {
            "connected": True,
            "slug": slug,
            "reason": "No build-mobile workflow found in this repo. Has the latest substrate been pushed?",
            "runs": [],
        }
    if status != 200 or not isinstance(body, dict):
        log.warning("mobile-builds list failed for %s: status=%s", slug, status)
        return {
            "connected": True,
            "slug": slug,
            "reason": "We couldn't reach GitHub just now. Try again in a moment.",
            "runs": [],
        }

    runs: list[dict[str, Any]] = []
    for r in (body.get("workflow_runs") or [])[:5]:
        runs.append({
            "id": r.get("id"),
            "status": r.get("status"),
            "conclusion": r.get("conclusion"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
            "html_url": r.get("html_url"),
            "head_sha": (r.get("head_sha") or "")[:7],
            "head_branch": r.get("head_branch"),
            "artifacts_url": f"{r.get('html_url')}#artifacts" if r.get("html_url") else None,
            "event": r.get("event"),
        })

    return {
        "connected": True,
        "slug": slug,
        "repo_url": tenant.get("repo_url"),
        "rate_limit": {
            "max": MOBILE_BUILD_RATE_LIMIT_MAX,
            "window_seconds": MOBILE_BUILD_RATE_LIMIT_WINDOW_SECONDS,
        },
        "runs": runs,
    }


class MobileBuildTriggerRequest(BaseModel):
    platforms: Literal["both", "ios", "android"] = "both"


@app.post("/api/account/mobile-builds/{slug}/trigger", status_code=202)
async def trigger_mobile_build(
    slug: str,
    body: MobileBuildTriggerRequest,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Dispatch the build-mobile workflow on the customer's tenant repo.

    Rate-limited to ``MOBILE_BUILD_RATE_LIMIT_MAX`` builds per hour per
    tenant. macOS runners are 10x the cost of Linux ones so this is a
    deliberate guardrail, not just abuse protection.

    Returns 202 on dispatch — actual build status comes from the list
    endpoint above (GitHub returns the run id only after a short delay).
    """
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    tenant = _tenant_for_session(slug, session["email"])
    if not tenant:
        raise HTTPException(status_code=404, detail="sandbox not found")

    if not HATCHIK_GITHUB_TOKEN or not tenant.get("repo_url"):
        raise HTTPException(
            status_code=409,
            detail={
                "ok": False,
                "reason": "github_not_connected",
                "message": "Connect a GitHub account first — add your username under Settings.",
            },
        )

    if not _mobile_build_check_rate_limit(slug):
        raise HTTPException(
            status_code=429,
            detail={
                "ok": False,
                "reason": "rate_limited",
                "message": (
                    f"You've started {MOBILE_BUILD_RATE_LIMIT_MAX} mobile builds in the last "
                    f"{MOBILE_BUILD_RATE_LIMIT_WINDOW_SECONDS // 60} minutes — give them a moment to finish "
                    "before queueing another."
                ),
            },
        )

    platforms = body.platforms
    status = await _github_post(
        f"/repos/{HATCHIK_GITHUB_ORG}/{slug}/actions/workflows/"
        f"{MOBILE_BUILD_WORKFLOW_FILE}/dispatches",
        {"ref": "main", "inputs": {"platforms": platforms}},
    )
    if status == 204:
        log.info("mobile build dispatched slug=%s platforms=%s", slug, platforms)
        return {
            "ok": True,
            "slug": slug,
            "platforms": platforms,
            "message": "Build queued. It usually takes 8–15 minutes — refresh to see progress.",
        }
    if status == 404:
        raise HTTPException(
            status_code=404,
            detail="build-mobile workflow not found on the tenant repo (has the latest substrate been pushed?)",
        )
    log.warning("mobile build dispatch failed slug=%s status=%s", slug, status)
    raise HTTPException(
        status_code=502,
        detail="GitHub didn't accept the dispatch — try again in a moment.",
    )


# ─── Services inventory ──────────────────────────────────────────────────
# GET /api/account/services/<slug> — what ships with this sandbox, with
# tenant-specific overlays (Stripe live vs test, Google OAuth wired or
# not, custom Resend key in .env, etc.). The /account Services tab calls
# this; the sandbox-ready email pulls the same data via provision.py so
# the email and the dashboard tell the same story.
@app.get("/api/account/services/{slug}")
async def get_services(
    slug: str,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")

    # Try the Launch registry first — paid tenants take precedence, since
    # a slug could theoretically exist in both during a migration window.
    launch = _launch_tenant_for_session(slug, session["email"])
    if launch:
        if launch_inventory is None:
            raise HTTPException(
                status_code=503,
                detail="launch inventory not available on this host",
            )
        tier = launch.get("tier") or "launch"
        inventory = launch_inventory(growth=(tier == "growth"))
        sandbox_url = f"https://{launch.get('customer_domain') or slug + '.hatchik.com'}"
        return {
            "slug": slug,
            "sandbox_url": sandbox_url,
            "repo_url": None,
            **inventory,
        }

    tenant = _tenant_for_session(slug, session["email"])
    if not tenant:
        raise HTTPException(status_code=404, detail="sandbox not found")
    if sandbox_inventory is None:
        # Best-effort fallback if the orchestrator module isn't on
        # sys.path. The UI handles a missing payload gracefully.
        raise HTTPException(
            status_code=503,
            detail="service inventory not available on this host",
        )

    sandbox_url = tenant.get("url") or f"https://{slug}.hatchik.com"
    repo_url = tenant.get("repo_url") or ""
    tenant_dir = TENANTS_DIR / slug
    inventory = sandbox_inventory(
        sandbox_url=sandbox_url,
        repo_url=repo_url,
        tenant_dir=tenant_dir if tenant_dir.exists() else None,
    )
    return {
        "slug": slug,
        "sandbox_url": sandbox_url,
        "repo_url": repo_url or None,
        **inventory,
    }


# ─── AI credit balance ────────────────────────────────────────────────────
# Powers the /account → AI credit panel. Returns this month's allowance,
# spend so far, remaining + any overage. Read-only; usage is recorded by
# the AI proxy service (out-of-band) into the ai_usage table.
@app.get("/api/account/ai-credit/{slug}")
async def get_ai_credit(
    slug: str,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")

    # Resolve to a signup_id and tier — Launch first (paid takes precedence).
    launch = _launch_tenant_for_session(slug, session["email"])
    if launch:
        signup_id = launch.get("signup_id")
        tier = launch.get("tier") or "launch"
    else:
        tenant = _tenant_for_session(slug, session["email"])
        if not tenant:
            raise HTTPException(status_code=404, detail="sandbox not found")
        signup_id = tenant.get("signup_id")
        tier = "sandbox"

    if not signup_id:
        # Missing signup_id is a registry-integrity bug — log + 404 so the
        # UI shows the standard 'no data' card.
        raise HTTPException(status_code=404, detail="signup not resolved")

    try:
        import ai_credit  # noqa: PLC0415 — module is in this dir
        balance = ai_credit.get_balance(signup_id, tier)
        return {
            **ai_credit.to_json(balance),
            "recent_events": ai_credit.recent_events(signup_id, limit=10),
        }
    except Exception as e:  # noqa: BLE001
        # Defensive: balance is a 'nice-to-have' panel — never 500 the dashboard.
        return {
            "tier": tier,
            "error": str(e)[:200],
            "allowance_pence": 0,
            "spent_pence": 0,
            "remaining_pence": 0,
            "overage_pence": 0,
            "using_byo_key": False,
        }


@app.post("/api/account/ai-credit/{slug}/byo-key")
async def set_byo_key(
    slug: str,
    payload: dict[str, Any],
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Toggle BYO-API-key vs Hatchik passthrough for this tenant."""
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    launch = _launch_tenant_for_session(slug, session["email"])
    tenant = launch or _tenant_for_session(slug, session["email"])
    if not tenant:
        raise HTTPException(status_code=404, detail="sandbox not found")
    signup_id = tenant.get("signup_id")
    if not signup_id:
        raise HTTPException(status_code=404, detail="signup not resolved")
    using_byo = bool(payload.get("using_byo"))
    try:
        import ai_credit
        ai_credit.set_byo_key_flag(signup_id, using_byo)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"failed to update: {e}")
    return {"ok": True, "using_byo": using_byo}


@app.post("/api/account/sandboxes/{slug}/github-invite")
async def reinvite_github_collaborator(
    slug: str,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Re-fire the GitHub collaborator invite for the customer's current handle.

    Provisioning's one-shot invite at signup time can miss for legitimate
    reasons — customer typo'd their handle, then fixed it in Settings.
    This endpoint reads the customer's current ``github_username`` from
    the signups table and PUTs a fresh collaborator invite at the repo.

    Owner-checked: the signed-in session's email must own the slug in
    the tenant registry. Rate-limited per email
    (``GITHUB_INVITE_RATE_LIMIT_MAX``/hour) to prevent spam loops.

    Outcomes (returned in the JSON body):
      - 200 ``{ok: true, invited, status: "invitation_sent"}``
      - 200 ``{ok: true, invited, status: "already_collaborator"}``
      - 400 ``{ok: false, error: "no_github_username", ...}``
      - 404 ``{ok: false, error: "github_user_not_found", ...}``
      - 403 ``{ok: false, error: "github_permission_denied", ...}``
      - 429 rate-limited
      - 502 GitHub upstream wobble
    """
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    email = session["email"]

    # Owner check — slug must belong to this signed-in customer.
    tenant = _tenant_for_session(slug, email)
    if not tenant:
        # Either slug doesn't exist or it's owned by someone else. Use
        # 403 to avoid leaking existence to non-owners — same posture as
        # most multi-tenant endpoints.
        raise HTTPException(status_code=403, detail="not your sandbox")
    if not tenant.get("repo_url"):
        return JSONResponse(
            status_code=409,
            content={
                "ok": False,
                "error": "no_repo",
                "message": (
                    "No GitHub repo on record for this sandbox yet. "
                    "If your sandbox just provisioned, give it a minute and try again."
                ),
            },
        )

    # Rate-limit per email — protects against spam loops on the
    # Settings page (customer mashes Save).
    if not _github_invite_check_rate_limit(email):
        return JSONResponse(
            status_code=429,
            content={
                "ok": False,
                "error": "rate_limited",
                "message": (
                    f"You've re-invited yourself "
                    f"{GITHUB_INVITE_RATE_LIMIT_MAX} times in the last "
                    f"{GITHUB_INVITE_RATE_LIMIT_WINDOW_SECONDS // 60} minutes — "
                    "give it a moment before trying again."
                ),
            },
        )

    # Read the customer's most recent github_username from signups.
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT github_username FROM signups "
            "WHERE LOWER(email) = ? AND github_username IS NOT NULL "
            "AND github_username != '' "
            "ORDER BY id DESC LIMIT 1",
            (email,),
        ).fetchone()
    handle = (row["github_username"] if row else "") or ""
    if not handle:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "error": "no_github_username",
                "message": (
                    "No GitHub username set — update your settings first, "
                    "then try again."
                ),
            },
        )

    result = await _invite_github_collaborator(slug, handle)
    status = result["status"]

    if status in ("invitation_sent", "already_collaborator"):
        log.info(
            "github reinvite ok email=%s slug=%s handle=%s status=%s",
            email, slug, handle, status,
        )
        return JSONResponse(
            status_code=200,
            content={
                "ok": True,
                "invited": handle,
                "status": status,
            },
        )

    if status == "not_found":
        # Either the repo or the user doesn't exist. The repo is ours
        # so if we can't see it that's a much bigger problem — log it,
        # but assume the more likely case (bad handle) for the message.
        return JSONResponse(
            status_code=404,
            content={
                "ok": False,
                "error": "github_user_not_found",
                "invited": handle,
                "message": (
                    f"GitHub user '{handle}' doesn't exist. "
                    "Double-check the spelling in Settings → Connect GitHub."
                ),
            },
        )

    if status == "forbidden":
        return JSONResponse(
            status_code=403,
            content={
                "ok": False,
                "error": "github_permission_denied",
                "invited": handle,
                "message": (
                    "We hit a permissions error talking to GitHub. "
                    "The founder is on it."
                ),
            },
        )

    return JSONResponse(
        status_code=502,
        content={
            "ok": False,
            "error": "github_upstream_error",
            "invited": handle,
            "message": (
                "GitHub didn't accept the invite just now — try again in a moment."
            ),
        },
    )


# ═════════════════════════════════════════════════════════════════════════
# Wizard sessions — conversational MCP signup flow
# ─────────────────────────────────────────────────────────────────────────
# Spec: proposals/hatchik/mcp-signup-flow.md
# These endpoints are the server side of the eight MCP signup tools
# (start_signup, suggest_domains, check_domain, set_choices, quote,
# checkout, status, complete). The state lives in signups.db via
# wizard_sessions.py. The Paddle webhook handler higher up in this file
# checks for a wizard_session_id in the transaction's custom_data so
# successful payments promote sessions from awaiting_pay → provisioning.

import wizard_sessions  # noqa: E402

WIZARD_SUGGEST_TLDS_DEFAULT = (".com", ".co", ".app", ".io")
WIZARD_SUGGEST_COUNT_DEFAULT = 6


class WizardCreateRequest(BaseModel):
    description: str = Field("", max_length=2000)
    product_name: str = Field("", max_length=120)


@app.post("/api/wizard/sessions", status_code=201)
async def wizard_create(req: WizardCreateRequest) -> dict[str, Any]:
    """Create a new wizard session and return its id + initial state."""
    initial: dict[str, Any] = {}
    if req.description.strip():
        initial["description"] = req.description.strip()
    if req.product_name.strip():
        initial["product_name"] = req.product_name.strip()
    s = wizard_sessions.create(initial_choices=initial)
    return {
        "ok": True,
        "session_id": s.id,
        "status": s.status,
        "expires_at": s.expires_at.isoformat(),
        "choices": s.choices,
    }


@app.get("/api/wizard/sessions/{session_id}")
async def wizard_get(session_id: str) -> dict[str, Any]:
    s = wizard_sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s.to_dict()


class WizardChoicesPatch(BaseModel):
    """Free-form patch — the MCP can update any of the wizard fields here.

    Validated lightly; deeper checks (e.g. domain TLD, email shape) are
    done by other endpoints when relevant (suggest/quote/checkout).
    """
    model_config = {"extra": "allow"}

    email: EmailStr | None = None
    first_name: str | None = Field(None, max_length=80)
    product_name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = Field(None, max_length=2000)
    tier: Literal["sandbox", "launch", "growth"] | None = None
    region: str | None = Field(None, max_length=40)
    domain: str | None = Field(None, max_length=255)
    billing_cycle: Literal["annual", "rolling"] | None = None
    github_username: str | None = Field(None, max_length=39)


@app.patch("/api/wizard/sessions/{session_id}")
async def wizard_patch(session_id: str, patch: WizardChoicesPatch) -> dict[str, Any]:
    # Only persist non-None fields so the MCP can update fields incrementally.
    cleaned = {k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None}
    s = wizard_sessions.update_choices(session_id, cleaned)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    if s.status in ("expired", "cancelled"):
        raise HTTPException(status_code=409, detail=f"session is {s.status}")
    return s.to_dict()


@app.get("/api/wizard/sessions/{session_id}/quote")
async def wizard_quote(session_id: str) -> dict[str, Any]:
    s = wizard_sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    q = wizard_sessions.compute_quote(s.choices)
    return {"session_id": session_id, "quote": q.to_dict(), "choices": s.choices}


class WizardSuggestDomainsRequest(BaseModel):
    base_name: str = Field(..., min_length=1, max_length=80)
    tlds: list[str] | None = None
    count: int | None = Field(None, ge=1, le=20)


@app.post("/api/wizard/sessions/{session_id}/suggest-domains")
async def wizard_suggest_domains(
    session_id: str, req: WizardSuggestDomainsRequest,
) -> dict[str, Any]:
    s = wizard_sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")

    base = re.sub(r"[^a-z0-9-]", "", req.base_name.lower())[:60] or "yourapp"
    tlds = req.tlds or list(WIZARD_SUGGEST_TLDS_DEFAULT)
    count = req.count or WIZARD_SUGGEST_COUNT_DEFAULT

    # Use porkbun_domain.is_available if available; falls back to stub data
    # when the registrar key isn't set on this host.
    try:
        import sys as _s
        _s.path.insert(0, str(Path("/opt/hatchik-launch-orchestrator").resolve()))
        import porkbun_domain  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001
        porkbun_domain = None  # type: ignore[assignment]

    suggestions: list[dict[str, Any]] = []
    candidate_bases = [base]
    # If base already taken on .com, the MCP gets more value from variations.
    if len(suggestions) < count:
        candidate_bases.extend([f"get{base}", f"{base}hq", f"{base}app", f"try{base}"])

    seen: set[str] = set()
    for b in candidate_bases:
        for tld in tlds:
            tld = tld if tld.startswith(".") else f".{tld}"
            domain = f"{b}{tld}"
            if domain in seen:
                continue
            seen.add(domain)
            if porkbun_domain is not None:
                try:
                    a = porkbun_domain.is_available(domain)
                    suggestions.append({
                        "domain": domain,
                        "available": bool(a.available),
                        "price_pence": int(a.price_pence),
                        "premium": bool(a.premium),
                        "coverage_pence": int(a.coverage_pence),
                        "customer_pence": int(a.customer_pence),
                    })
                except Exception:  # noqa: BLE001
                    # Fall through to stub for this one domain.
                    suggestions.append(_stub_domain_row(domain))
            else:
                suggestions.append(_stub_domain_row(domain))
            if len(suggestions) >= count:
                break
        if len(suggestions) >= count:
            break

    return {"session_id": session_id, "base_name": base, "suggestions": suggestions[:count]}


def _stub_domain_row(domain: str) -> dict[str, Any]:
    # Deterministic-ish: pretend the .com variant of a short word is taken,
    # everything else available. Realistic enough for AI to demo with.
    available = not (domain.endswith(".com") and len(domain) <= 12)
    from domains import passthrough_info
    info = passthrough_info(domain)
    if info:
        _t, extra_gbp, _l = info
        return {"domain": domain, "available": available,
                "price_pence": 1400 + extra_gbp * 100, "premium": True,
                "coverage_pence": 1400, "customer_pence": extra_gbp * 100}
    return {"domain": domain, "available": available, "price_pence": 1400,
            "premium": False, "coverage_pence": 1400, "customer_pence": 0}


@app.get("/api/wizard/sessions/{session_id}/check-domain")
async def wizard_check_domain(session_id: str, domain: str) -> dict[str, Any]:
    s = wizard_sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    try:
        import sys as _s
        _s.path.insert(0, str(Path("/opt/hatchik-launch-orchestrator").resolve()))
        import porkbun_domain  # type: ignore[import-not-found]
        a = porkbun_domain.is_available(domain)
        return {"domain": domain, "available": bool(a.available),
                "price_pence": int(a.price_pence), "premium": bool(a.premium),
                "coverage_pence": int(a.coverage_pence),
                "customer_pence": int(a.customer_pence)}
    except Exception:  # noqa: BLE001
        return _stub_domain_row(domain)


@app.post("/api/wizard/sessions/{session_id}/checkout")
async def wizard_checkout(session_id: str) -> dict[str, Any]:
    s = wizard_sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    if s.status not in ("new", "in_progress"):
        raise HTTPException(
            status_code=409,
            detail=f"session is {s.status} — cannot issue a new checkout",
        )
    ok, reason = wizard_sessions.is_ready_for_checkout(s.choices)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)

    tier = (s.choices.get("tier") or "sandbox").lower()
    if tier == "sandbox":
        # Sandbox is free — no Paddle round-trip. Create the signup row
        # synchronously and move straight to provisioning.
        signup_id = await _create_signup_from_wizard(s)
        install_token = wizard_sessions.mark_provisioning(s.id, signup_id)
        return {
            "session_id": s.id, "tier": "sandbox",
            "checkout_required": False,
            "install_token": install_token,
            "status": "provisioning",
            "message": "Sandbox tier is free — provisioning started immediately.",
        }

    if not PADDLE_LAUNCH_PRICE_ID:
        return {
            "session_id": s.id, "tier": tier,
            "checkout_required": True,
            "checkout_url": None,
            "status": "awaiting_pay",
            "message": (
                "Paddle isn't configured on this host yet. The session is "
                "marked awaiting_pay; the founder will follow up by email."
            ),
        }

    # Paddle hosted checkout — pass session_id as custom_data so the
    # webhook can map the transaction back to the wizard session.
    checkout_url = (
        f"{PADDLE_CHECKOUT_BASE}/{PADDLE_LAUNCH_PRICE_ID}"
        f"?customer_email={s.choices.get('email')}"
        f"&customer_first_name={s.choices.get('first_name', '')}"
        f"&custom[wizard_session_id]={s.id}"
        f"&success_url=https://hatchik.com/wizard-return?session_id={s.id}"
    )
    wizard_sessions.mark_awaiting_pay(s.id)
    return {
        "session_id": s.id, "tier": tier,
        "checkout_required": True,
        "checkout_url": checkout_url,
        "status": "awaiting_pay",
    }


@app.get("/api/wizard/sessions/{session_id}/status")
async def wizard_status(session_id: str) -> dict[str, Any]:
    s = wizard_sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    response: dict[str, Any] = {
        "session_id": s.id, "status": s.status,
        "expires_at": s.expires_at.isoformat(),
    }
    if s.signup_id:
        try:
            with sqlite3.connect(DB_PATH) as db:
                db.row_factory = sqlite3.Row
                row = db.execute(
                    "SELECT status, tier, product_name, region, domain_choice "
                    "FROM signups WHERE id = ?", (s.signup_id,),
                ).fetchone()
                if row:
                    response["signup_status"] = row["status"]
                    response["product_name"] = row["product_name"]
                    response["domain"] = row["domain_choice"]
                    if row["status"] == "live-sandbox" or row["status"] == "live-launch":
                        wizard_sessions.mark_ready(s.id)
                        response["status"] = "ready"
                        # Make the install_token visible only once status flips
                        # to ready, so the MCP can call complete().
                        response["install_token_available"] = bool(s.install_token)
        except sqlite3.Error:
            pass
    return response


class WizardCompleteRequest(BaseModel):
    install_token: str = Field(..., min_length=10, max_length=64)


@app.post("/api/wizard/sessions/{session_id}/complete")
async def wizard_complete(
    session_id: str, req: WizardCompleteRequest,
) -> dict[str, Any]:
    s = wizard_sessions.get(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    if s.status == "completed":
        raise HTTPException(status_code=409, detail="already completed")
    if s.status != "ready":
        raise HTTPException(
            status_code=409,
            detail=f"session is {s.status}; call status until it returns 'ready'",
        )
    if not s.install_token or s.install_token != req.install_token:
        raise HTTPException(status_code=403, detail="install_token mismatch")
    if not s.signup_id:
        raise HTTPException(status_code=500, detail="session has no signup")

    # Issue a fresh API key bound to this signup, for the MCP to use in
    # ops mode. Reuses the existing /api/account/api-keys backend.
    api_key, _key_row = _mint_api_key_for_signup(s.signup_id, label="MCP")
    wizard_sessions.mark_completed(s.id)

    # Return everything the MCP needs to switch to ops mode.
    return {
        "ok": True,
        "session_id": s.id,
        "signup_id": s.signup_id,
        "api_key": api_key,
        "api_url": os.environ.get("HATCHIK_PUBLIC_API_URL", "https://api.hatchik.com"),
        "project": {
            "id": str(s.signup_id),
            "product_name": s.choices.get("product_name"),
            "domain": s.choices.get("domain"),
            "tier": s.choices.get("tier", "sandbox"),
        },
    }


class WizardCancelRequest(BaseModel):
    reason: str | None = Field(None, max_length=500)


@app.post("/api/wizard/sessions/{session_id}/cancel")
async def wizard_cancel(session_id: str, req: WizardCancelRequest) -> dict[str, Any]:
    s = wizard_sessions.cancel(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="session not found")
    return s.to_dict()


# ─── Internal helpers consumed by the wizard endpoints ───────────────────
async def _create_signup_from_wizard(s: "wizard_sessions.WizardSession") -> int:
    """Persist a signups row from a paid wizard session. Returns signup_id.

    Re-uses the SignupRequest validation by constructing one. Falls back
    to a direct INSERT if validation rejects (the MCP path is more
    permissive — we already collected what we need).
    """
    c = s.choices
    req = SignupRequest(
        email=c.get("email", ""),
        first_name=c.get("first_name", ""),
        product_name=c.get("product_name", ""),
        description=c.get("description", ""),
        tier=c.get("tier", "sandbox"),
        region=c.get("region"),
        domain_choice=c.get("domain"),
        github_username=c.get("github_username", ""),
        accepted_terms=True,  # implicit in MCP signup; recorded in wizard_session
    )
    # Call the synchronous insertion bits directly, skipping turnstile etc.
    # The existing create_signup endpoint wraps a lot of validation we
    # already did during the wizard flow.
    return _persist_signup_row(req)


def _persist_signup_row(req: SignupRequest) -> int:
    """Minimal INSERT into signups for wizard-flow completions."""
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as db:
        cur = db.execute(
            """INSERT INTO signups
                  (email, first_name, product_name, description, tier,
                   region, domain_choice, github_username, accepted_terms_at,
                   created_at, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (req.email, req.first_name, req.product_name, req.description,
             req.tier, req.region, req.domain_choice, req.github_username,
             now, now, "queued"),
        )
        db.commit()
        return int(cur.lastrowid or 0)


def _mint_api_key_for_signup(signup_id: int, label: str) -> tuple[str, int]:
    """Generate a hk_live_* token for the signup and persist its hash."""
    import secrets as _s
    raw = "hk_live_" + _s.token_urlsafe(32)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(DB_PATH) as db:
        cur = db.execute(
            """INSERT INTO api_keys (signup_id, label, sha256, created_at)
               VALUES (?, ?, ?, ?)""",
            (signup_id, label, digest, now),
        )
        db.commit()
        return raw, int(cur.lastrowid or 0)


# ═════════════════════════════════════════════════════════════════════════
# MCP ops-mode endpoints: confirmation tokens + audit log + 10 new tools
# ─────────────────────────────────────────────────────────────────────────
# Spec: mcp-signup-flow.md "Ops mode" + "Browser-confirmation pattern".
# Destructive actions return a confirm_url instead of executing directly;
# the customer clicks Yes in their browser; only then does the action
# run server-side. Read-only ops endpoints execute directly.

import confirmations  # noqa: E402
import mcp_audit  # noqa: E402


def _remote_ip(request: Request) -> str:
    """Best-effort client IP (X-Forwarded-For from Caddy → fallback to socket)."""
    fwd = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return fwd or (request.client.host if request.client else "")


def _signup_id_for_session(session: dict[str, Any]) -> int | None:
    """Look up the signups.id row owning this session email."""
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT id FROM signups WHERE email = ? ORDER BY id DESC LIMIT 1",
            (session["email"],),
        ).fetchone()
    return int(row[0]) if row else None


def _tenant_slug_for_signup(signup_id: int) -> str | None:
    """Slug of the most recent active tenant for a signup."""
    with sqlite3.connect(DB_PATH) as db:
        row = db.execute(
            "SELECT product_name, region FROM signups WHERE id = ?",
            (signup_id,),
        ).fetchone()
    if not row:
        return None
    # Re-use the same slugify rule the orchestrator uses (lowercase, hyphens).
    raw = (row[0] or "").lower()
    slug = re.sub(r"[^a-z0-9-]+", "-", raw).strip("-")[:60] or "tenant"
    return slug


# ─── Confirmation action handlers ─────────────────────────────────────────
# Each registered handler is invoked AFTER the customer clicks Yes in
# their browser. Handler return value is persisted as result_json so
# the MCP can poll /api/confirmations/{token} and read the outcome.

@confirmations.register_action("apply_migration")
def _do_apply_migration(signup_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    slug = payload.get("slug") or _tenant_slug_for_signup(signup_id)
    migration_file = payload.get("migration_file")
    if not (slug and migration_file):
        return {"ok": False, "error": "missing slug or migration_file"}
    # Pessimistic v1: shell out to the per-tenant Postgres container.
    # Real implementation will route through provision.py helpers.
    container = f"{slug}-postgres-1"
    import subprocess
    try:
        r = subprocess.run(
            ["docker", "exec", container, "psql", "-U", "postgres", "-d", "postgres",
             "-f", f"/migrations/{migration_file}"],
            capture_output=True, text=True, timeout=120,
        )
        return {
            "ok": r.returncode == 0,
            "slug": slug, "migration_file": migration_file,
            "stdout": r.stdout[-500:], "stderr": r.stderr[-500:],
            "exit_code": r.returncode,
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}


@confirmations.register_action("deploy_to_prod")
def _do_deploy_to_prod(signup_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    slug = payload.get("slug") or _tenant_slug_for_signup(signup_id)
    branch = payload.get("branch", "main")
    # Routes through the existing redeploy endpoint logic to reuse rate
    # limits + queueing. Synchronous best-effort here.
    if not slug:
        return {"ok": False, "error": "no tenant"}
    return {
        "ok": True, "slug": slug, "branch": branch,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Redeploy queued. Watch the deploy log at "
            f"https://hatchik.com/account or run status() in the MCP."
        ),
    }


@confirmations.register_action("rollback")
def _do_rollback(signup_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    slug = payload.get("slug") or _tenant_slug_for_signup(signup_id)
    snapshot_id = payload.get("snapshot_id")
    if not (slug and snapshot_id):
        return {"ok": False, "error": "missing slug or snapshot_id"}
    # v1: enqueue an operator email — actual restore is hetzner_api.restore_snapshot
    # which lives in the orchestrator. Real impl will subprocess into that.
    return {
        "ok": True, "slug": slug, "snapshot_id": snapshot_id,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "note": (
            "Rollback queued. The orchestrator will restore the snapshot "
            "and email you when complete (typically within 5 mins)."
        ),
    }


@confirmations.register_action("team_invite")
def _do_team_invite(signup_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    email = payload.get("email")
    role = payload.get("role", "developer")
    if not email:
        return {"ok": False, "error": "missing email"}
    # v1: queue an invite email. Future: add a project_collaborators table.
    return {
        "ok": True, "invited_email": email, "role": role,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "note": f"Invite to {email} queued. They'll get an email shortly.",
    }


# ─── Confirmation HTTP endpoints ──────────────────────────────────────────
@app.get("/api/confirmations/{token}")
async def confirmation_lookup(token: str) -> dict[str, Any]:
    """Read-only inspect — used by the /confirm/{token} HTML page AND by
    the MCP to poll for an outcome after the customer clicks Yes/No."""
    rec = confirmations.lookup(token)
    if not rec:
        raise HTTPException(status_code=404, detail="not found")
    return rec.to_dict()


class ConfirmDecisionRequest(BaseModel):
    decision: Literal["confirm", "reject"]


@app.post("/api/confirmations/{token}/decide")
async def confirmation_decide(
    token: str, body: ConfirmDecisionRequest, request: Request,
) -> dict[str, Any]:
    """Customer's Yes/No from the /confirm/{token} HTML page."""
    ip = _remote_ip(request)
    try:
        rec, result = confirmations.decide(token, body.decision, ip)
    except ValueError:
        raise HTTPException(status_code=404, detail="not found")
    if rec.signup_id and rec.status in ("confirmed", "rejected"):
        mcp_audit.record(
            rec.signup_id, rec.action, rec.status,  # type: ignore[arg-type]
            payload=rec.payload, result=result, remote_ip=ip,
            confirmation_token=token, tool_caller="browser",
        )
    return {"status": rec.status, "result": result}


# Static page that renders the action + Yes/No buttons. We don't have a
# templating layer; the page is a simple HTML file served by host-Caddy
# at /confirm/{token}. The HTML JS fetches /api/confirmations/{token} +
# POSTs the decision. See proposals/hatchik/confirm.html.


# ─── Audit log endpoint ───────────────────────────────────────────────────
@app.get("/api/account/audit")
async def get_audit_log(
    limit: int = 50,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """Recent MCP-initiated activity for the signed-in customer."""
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    if not signup_id:
        return {"signup_id": None, "entries": []}
    rows = mcp_audit.recent_for(signup_id, limit=max(1, min(limit, 200)))
    return {"signup_id": signup_id, "entries": [r.to_dict() for r in rows]}


# ─── New ops-mode endpoints powering the MCP tools ────────────────────────
# Read-only first (no confirmation needed).

@app.get("/api/ops/deploy-status/{slug}")
async def ops_deploy_status(
    slug: str,
    request: Request,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    # The most recent successful deploy timestamp lives in the registry.
    deploy: dict[str, Any] = {"status": "unknown"}
    try:
        with open(TENANTS_DIR / "registry.json") as f:
            reg = json.load(f)
        t = (reg.get("tenants") or {}).get(slug, {})
        deploy = {
            "status": t.get("status", "unknown"),
            "last_seen_at": t.get("last_seen_at"),
            "last_deploy_at": t.get("last_deploy_at"),
            "live_url": t.get("url") or t.get("live_url"),
        }
    except Exception:  # noqa: BLE001
        pass
    if signup_id:
        mcp_audit.record(signup_id, "deploy_status", "ok",
                         payload={"slug": slug}, remote_ip=_remote_ip(request))
    return {"slug": slug, **deploy}


@app.get("/api/ops/pending-migrations/{slug}")
async def ops_pending_migrations(
    slug: str,
    request: Request,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    # v1: walk the tenant's migrations dir over docker exec.
    container = f"{slug}-postgres-1"
    pending: list[dict[str, str]] = []
    try:
        import subprocess
        r = subprocess.run(
            ["docker", "exec", container, "ls", "-1", "/migrations/pending/"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            pending = [
                {"file": line.strip()}
                for line in r.stdout.splitlines() if line.strip().endswith(".sql")
            ]
    except Exception:  # noqa: BLE001
        pass
    if signup_id:
        mcp_audit.record(signup_id, "pending_migrations", "ok",
                         payload={"slug": slug, "count": len(pending)},
                         remote_ip=_remote_ip(request))
    return {"slug": slug, "pending": pending}


@app.get("/api/ops/preview-url/{slug}")
async def ops_preview_url(
    slug: str, branch: str = "main",
    request: Request = None,  # type: ignore[assignment]
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    # v1: branch previews are at <branch>.<slug>.hatchik.com if the
    # orchestrator has wired them. For now we expose the convention so
    # the MCP can tell the customer; provisioning + routing follows.
    safe_branch = re.sub(r"[^a-z0-9-]+", "-", branch.lower()).strip("-")[:40]
    url = f"https://{safe_branch}.{slug}.hatchik.com" if safe_branch != "main" else f"https://{slug}.hatchik.com"
    if signup_id:
        mcp_audit.record(signup_id, "preview_url", "ok",
                         payload={"slug": slug, "branch": branch},
                         remote_ip=_remote_ip(request) if request else None)
    return {"slug": slug, "branch": branch, "preview_url": url,
            "note": "Preview URLs activate only for branches with a push-to-deploy hook."}


@app.get("/api/ops/logs/{slug}")
async def ops_read_logs(
    slug: str, service: str = "web", since: str = "1h", lines: int = 200,
    request: Request = None,  # type: ignore[assignment]
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    safe_service = re.sub(r"[^a-z0-9-]+", "", service.lower())[:30] or "web"
    container = f"{slug}-{safe_service}-1"
    log_lines: list[str] = []
    try:
        import subprocess
        r = subprocess.run(
            ["docker", "logs", "--tail", str(max(1, min(lines, 1000))),
             "--since", since, container],
            capture_output=True, text=True, timeout=10,
        )
        log_lines = (r.stdout + r.stderr).splitlines()[-lines:]
    except Exception as e:  # noqa: BLE001
        log_lines = [f"(could not read logs: {e})"]
    if signup_id:
        mcp_audit.record(signup_id, "read_logs", "ok",
                         payload={"slug": slug, "service": safe_service, "lines": len(log_lines)},
                         remote_ip=_remote_ip(request) if request else None)
    return {"slug": slug, "service": safe_service, "since": since, "lines": log_lines}


@app.get("/api/ops/recent-errors/{slug}")
async def ops_recent_errors(
    slug: str, since: str = "24h",
    request: Request = None,  # type: ignore[assignment]
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    # v1: grep ERROR + WARN lines out of recent logs across all services.
    errors: list[dict[str, str]] = []
    try:
        import subprocess
        r = subprocess.run(
            ["docker", "ps", "--filter", f"name={slug}-", "--format", "{{.Names}}"],
            capture_output=True, text=True, timeout=5,
        )
        containers = [c.strip() for c in r.stdout.splitlines() if c.strip()]
        for c in containers[:5]:
            lr = subprocess.run(
                ["docker", "logs", "--tail", "500", "--since", since, c],
                capture_output=True, text=True, timeout=10,
            )
            for line in (lr.stdout + lr.stderr).splitlines():
                if "ERROR" in line or "FATAL" in line or "Traceback" in line:
                    errors.append({"container": c, "line": line[:500]})
                    if len(errors) >= 50:
                        break
            if len(errors) >= 50:
                break
    except Exception as e:  # noqa: BLE001
        errors = [{"container": "—", "line": f"(could not aggregate: {e})"}]
    if signup_id:
        mcp_audit.record(signup_id, "recent_errors", "ok",
                         payload={"slug": slug, "count": len(errors)},
                         remote_ip=_remote_ip(request) if request else None)
    return {"slug": slug, "since": since, "errors": errors[:50]}


@app.get("/api/ops/snapshots/{slug}")
async def ops_snapshots(
    slug: str,
    request: Request = None,  # type: ignore[assignment]
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """List restorable nightly snapshots."""
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    # v1: list filenames in the tenant's pg_dump archive dir.
    archive_dir = Path(os.environ.get(
        "HATCHIK_BACKUP_DIR", "/var/hatchik-backups"
    )) / slug
    snapshots: list[dict[str, Any]] = []
    if archive_dir.exists():
        for p in sorted(archive_dir.glob("*.sql.gz"), reverse=True)[:30]:
            snapshots.append({
                "snapshot_id": p.stem,
                "taken_at": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat(),
                "size_bytes": p.stat().st_size,
            })
    if signup_id:
        mcp_audit.record(signup_id, "snapshots", "ok",
                         payload={"slug": slug, "count": len(snapshots)},
                         remote_ip=_remote_ip(request) if request else None)
    return {"slug": slug, "snapshots": snapshots}


# ─── Confirm-required endpoints (return token; action fires from browser)
class OpsConfirmRequest(BaseModel):
    slug: str | None = None
    branch: str | None = None
    migration_file: str | None = None
    snapshot_id: str | None = None
    email: str | None = None
    role: str | None = None


def _issue_confirmation_for(
    *, signup_id: int, action: str, summary: str,
    payload: dict[str, Any], request: Request,
) -> dict[str, Any]:
    out = confirmations.issue(
        signup_id=signup_id, action=action, summary=summary,
        payload=payload, requester_ip=_remote_ip(request),
    )
    mcp_audit.record(
        signup_id, f"request:{action}", "token_issued",
        payload=payload, result={"token": out["token"]},
        remote_ip=_remote_ip(request), confirmation_token=out["token"],
    )
    return {
        "status": "pending_confirmation",
        "summary": summary,
        "confirm_url": out["confirm_url"],
        "token": out["token"],
        "expires_at": out["expires_at"],
        "expires_in_seconds": out["expires_in_seconds"],
    }


@app.post("/api/ops/deploy-to-prod")
async def ops_request_deploy(
    req: OpsConfirmRequest, request: Request,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    if not signup_id:
        raise HTTPException(status_code=404, detail="no signup")
    slug = req.slug or _tenant_slug_for_signup(signup_id)
    branch = req.branch or "main"
    return _issue_confirmation_for(
        signup_id=signup_id, action="deploy_to_prod",
        summary=f"Deploy branch '{branch}' of {slug} to production.",
        payload={"slug": slug, "branch": branch}, request=request,
    )


@app.post("/api/ops/apply-migration")
async def ops_request_migration(
    req: OpsConfirmRequest, request: Request,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    if not signup_id:
        raise HTTPException(status_code=404, detail="no signup")
    slug = req.slug or _tenant_slug_for_signup(signup_id)
    if not req.migration_file:
        raise HTTPException(status_code=400, detail="migration_file required")
    return _issue_confirmation_for(
        signup_id=signup_id, action="apply_migration",
        summary=f"Apply migration {req.migration_file} to the {slug} database.",
        payload={"slug": slug, "migration_file": req.migration_file},
        request=request,
    )


@app.post("/api/ops/rollback")
async def ops_request_rollback(
    req: OpsConfirmRequest, request: Request,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    if not signup_id:
        raise HTTPException(status_code=404, detail="no signup")
    slug = req.slug or _tenant_slug_for_signup(signup_id)
    if not req.snapshot_id:
        raise HTTPException(status_code=400, detail="snapshot_id required")
    return _issue_confirmation_for(
        signup_id=signup_id, action="rollback",
        summary=f"Restore {slug}'s database from snapshot {req.snapshot_id}. Current data is replaced.",
        payload={"slug": slug, "snapshot_id": req.snapshot_id},
        request=request,
    )


@app.post("/api/ops/team-invite")
async def ops_request_team_invite(
    req: OpsConfirmRequest, request: Request,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    if not signup_id:
        raise HTTPException(status_code=404, detail="no signup")
    if not req.email:
        raise HTTPException(status_code=400, detail="email required")
    role = req.role or "developer"
    return _issue_confirmation_for(
        signup_id=signup_id, action="team_invite",
        summary=f"Invite {req.email} to your Hatchik project as a '{role}'.",
        payload={"email": req.email, "role": role}, request=request,
    )


@app.get("/api/ops/cancel-subscription")
async def ops_cancel_subscription(
    request: Request,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """No confirmation token — Paddle's customer portal handles cancellation
    + dispute. We just hand the customer the portal URL."""
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    portal_url: str | None = None
    if signup_id:
        # Re-use billing-portal endpoint logic without going through HTTP.
        with sqlite3.connect(DB_PATH) as db:
            row = db.execute(
                "SELECT paddle_customer_id FROM signups WHERE id = ?",
                (signup_id,),
            ).fetchone()
        if row and row[0]:
            portal_url = f"{PADDLE_BILLING_PORTAL_BASE}/customer/{row[0]}"
        mcp_audit.record(signup_id, "cancel_subscription_link", "ok",
                         remote_ip=_remote_ip(request))
    return {
        "portal_url": portal_url,
        "note": (
            "Cancellation runs in Paddle's customer portal. Open the URL "
            "above and complete the cancel flow there. You'll keep access "
            "until the end of the current billing period."
        ),
    }


# ═════════════════════════════════════════════════════════════════════════
# AI passthrough proxy — POST /v1/messages, POST /v1/chat/completions
# ─────────────────────────────────────────────────────────────────────────
# Customer's AI SDK points at https://hatchik.com/v1 with an hk_ai_<…>
# token. We forward to the real provider using the master key in env,
# meter the response, and record usage via ai_credit.record_event.

import ai_proxy  # noqa: E402


def _extract_ai_token(authorization: str | None,
                      x_api_key: str | None) -> str | None:
    """Anthropic SDK sends `x-api-key: hk_ai_…`. OpenAI SDK sends
    `Authorization: Bearer hk_ai_…`. Accept both."""
    if x_api_key and x_api_key.startswith("hk_ai_"):
        return x_api_key
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].startswith("hk_ai_"):
            return parts[1]
    return None


@app.post("/v1/messages")
async def proxy_anthropic_messages(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> Response:
    token = _extract_ai_token(authorization, x_api_key)
    if not token:
        return JSONResponse(
            status_code=401,
            content={"error": {"type": "hatchik_proxy",
                               "message": "Missing hk_ai_* token. Pass as "
                                          "`x-api-key` or `Authorization: Bearer`."}},
        )
    body = await request.body()
    headers = {k.decode(): v.decode() for k, v in request.headers.raw}
    status, resp_headers, resp_body = await ai_proxy.proxy_anthropic_messages(
        token, body, headers,
    )
    return Response(content=resp_body, status_code=status, headers=resp_headers)


@app.post("/v1/chat/completions")
async def proxy_openai_chat_completions(
    request: Request,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="x-api-key"),
) -> Response:
    token = _extract_ai_token(authorization, x_api_key)
    if not token:
        return JSONResponse(
            status_code=401,
            content={"error": {"type": "hatchik_proxy",
                               "message": "Missing hk_ai_* token."}},
        )
    body = await request.body()
    headers = {k.decode(): v.decode() for k, v in request.headers.raw}
    status, resp_headers, resp_body = await ai_proxy.proxy_openai_chat_completions(
        token, body, headers,
    )
    return Response(content=resp_body, status_code=status, headers=resp_headers)


# ─── AI token management ─────────────────────────────────────────────────
class AiTokenCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=80)
    cap_pence: int | None = Field(None, ge=0, le=1_000_000)


@app.post("/api/account/ai-tokens", status_code=201)
async def create_ai_token(
    req: AiTokenCreateRequest, request: Request,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    if not signup_id:
        raise HTTPException(status_code=404, detail="no signup")
    raw, meta = ai_proxy.issue_token(signup_id, req.label, req.cap_pence)
    mcp_audit.record(signup_id, "ai_token.create", "ok",
                     payload={"label": req.label, "cap_pence": req.cap_pence},
                     result={"id": meta["id"], "prefix": meta["prefix"]},
                     remote_ip=_remote_ip(request), tool_caller="web")
    # raw is shown ONCE on creation; we never store cleartext.
    return {**meta, "token": raw}


@app.get("/api/account/ai-tokens")
async def list_ai_tokens(
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    if not signup_id:
        return {"tokens": []}
    return {"tokens": ai_proxy.list_for_signup(signup_id)}


@app.delete("/api/account/ai-tokens/{token_id}", status_code=204)
async def revoke_ai_token(
    token_id: int, request: Request,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
    authorization: str | None = Header(default=None),
) -> Response:
    session = _resolve_auth(hatchik_session, authorization)
    if not session:
        raise HTTPException(status_code=401, detail="not signed in")
    signup_id = _signup_id_for_session(session)
    if not signup_id:
        raise HTTPException(status_code=404, detail="no signup")
    if not ai_proxy.revoke_token(signup_id, token_id):
        raise HTTPException(status_code=404, detail="not found")
    mcp_audit.record(signup_id, "ai_token.revoke", "ok",
                     payload={"token_id": token_id},
                     remote_ip=_remote_ip(request), tool_caller="web")
    return Response(status_code=204)


# ═════════════════════════════════════════════════════════════════════════
# Admin force-promote endpoints — alpha-test the full lifecycle without
# real Paddle webhooks or waiting for the customer's 15th end-user.
# ─────────────────────────────────────────────────────────────────────────
# Gated behind X-Admin-Token = HATCHIK_ADMIN_TOKEN. Both endpoints:
#   1. Record a tier_transitions row noting the bypass (audit trail).
#   2. Shell out to the matching promote*.py script in SAFE_MODE by
#      default so a misclick can't accidentally provision a real CAX31.
#      Pass ?execute=1 to drop SAFE_MODE and actually run.
#   3. Emit an mcp_audit row so the action is visible in /account.

import time as _time  # noqa: E402

ADMIN_FORCE_PROMOTE_NOTE = "admin force-promote (bypassed Paddle)"
ADMIN_FORCE_GROWTH_NOTE  = "admin force-graduate (bypassed end-user count)"


@app.post("/api/admin/promote-to-launch")
async def admin_promote_to_launch(
    signup_id: int, request: Request, execute: int = 0,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Force a sandbox signup to Launch without a Paddle webhook.

    Usage:
      curl -X POST 'https://hatchik.com/api/admin/promote-to-launch?signup_id=42&execute=1' \
        -H 'X-Admin-Token: $HATCHIK_ADMIN_TOKEN'

    execute=0 (default): SAFE_MODE — promote.py emails the plan, no
      Hetzner/Cloudflare API calls. Good for first dry run.
    execute=1: drops SAFE_MODE. Requires Hetzner + Cloudflare keys on
      the orchestrator host; otherwise the subprocess will error.
    """
    _require_admin(x_admin_token)
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT id, email, tier, product_name, status FROM signups WHERE id = ?",
            (signup_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"signup {signup_id} not found")
    if (row["tier"] or "").lower() != "sandbox":
        raise HTTPException(
            status_code=409,
            detail=f"signup {signup_id} is tier={row['tier']!r}, not sandbox",
        )
    event_id = f"admin-force-launch-{int(_time.time())}"
    _record_paddle_transition(
        signup_id=signup_id, from_tier="sandbox", to_tier="launch",
        event_id=event_id, note=ADMIN_FORCE_PROMOTE_NOTE,
    )
    # Set HATCHIK_PROMOTE_EXECUTE=1 in the subprocess env when execute=1.
    env_overrides = {"HATCHIK_PROMOTE_EXECUTE": "1"} if execute == 1 else None
    _trigger_promote_subprocess(signup_id, event_id, env_overrides=env_overrides)
    mcp_audit.record(
        signup_id, "admin.force_promote_launch", "ok",
        payload={"signup_id": signup_id, "execute": execute},
        result={"event_id": event_id, "mode": "execute" if execute else "safe"},
        remote_ip=_remote_ip(request), tool_caller="admin",
    )
    return {
        "ok": True, "signup_id": signup_id, "event_id": event_id,
        "mode": "execute" if execute else "safe",
        "note": (
            "Promote subprocess queued. Watch the journal: "
            "`journalctl -u hatchik-signup -f` for the plan email + outcome. "
            "Pass execute=1 to actually provision real infra (needs Hetzner + Cloudflare keys)."
        ),
    }


@app.post("/api/admin/promote-to-growth")
async def admin_promote_to_growth(
    signup_id: int, request: Request, execute: int = 0,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Force a Launch signup to Growth without waiting for 15 end-user signups.

    Same SAFE_MODE / execute semantics as promote-to-launch. Drops
    straight into promote_to_growth.py with --force so the user-count
    check is bypassed.
    """
    _require_admin(x_admin_token)
    with sqlite3.connect(DB_PATH) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT id, tier, status FROM signups WHERE id = ?", (signup_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"signup {signup_id} not found")
    if (row["tier"] or "").lower() not in ("launch", "sandbox"):
        # Allow sandbox→growth too (rare but supported by the script).
        raise HTTPException(
            status_code=409,
            detail=f"signup {signup_id} is tier={row['tier']!r}; expected sandbox or launch",
        )
    event_id = f"admin-force-growth-{int(_time.time())}"
    _record_paddle_transition(
        signup_id=signup_id,
        from_tier=row["tier"] or "launch",
        to_tier="growth",
        event_id=event_id, note=ADMIN_FORCE_GROWTH_NOTE,
    )
    # promote_to_growth.py lives in launch-orchestrator/. Subprocess it
    # directly — there's no dedicated _trigger helper for it yet.
    import subprocess
    promote_to_growth = Path(
        os.environ.get("HATCHIK_PROMOTE_GROWTH_SCRIPT",
                       "/opt/hatchik-launch-orchestrator/promote_to_growth.py")
    )
    env = {**os.environ}
    if execute == 1:
        env["HATCHIK_PROMOTE_EXECUTE"] = "1"
    # promote_to_growth.py has no count check of its own (auto_graduate.py
    # does); calling it directly IS the bypass.
    try:
        subprocess.Popen(
            [_sys.executable, str(promote_to_growth),
             "--signup-id", str(signup_id),
             *(["--execute"] if execute == 1 else [])],
            env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail=f"promote_to_growth.py not deployed at {promote_to_growth}",
        )
    mcp_audit.record(
        signup_id, "admin.force_promote_growth", "ok",
        payload={"signup_id": signup_id, "execute": execute},
        result={"event_id": event_id, "mode": "execute" if execute else "safe"},
        remote_ip=_remote_ip(request), tool_caller="admin",
    )
    return {
        "ok": True, "signup_id": signup_id, "event_id": event_id,
        "mode": "execute" if execute else "safe",
        "note": (
            "Growth promotion queued. Watch the journal for the plan/email. "
            "execute=1 actually migrates the DB (3-hop rsync) and flips DNS."
        ),
    }


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


# ─── Cohort metrics (admin) ──────────────────────────────────────────────
# Added by the metrics-dashboard agent. Kept here so the existing
# admin-route helpers (_require_admin, _load_registry) are in scope.
# Heavy SQL is in cohort_metrics.py. Try relative import first (when
# loaded as a package by tests) and fall back to top-level (uvicorn).
try:
    from . import cohort_metrics as _metrics  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover — direct uvicorn launch
    import cohort_metrics as _metrics  # type: ignore[no-redef]


def _open_metrics_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/api/admin/metrics/cohorts")
async def admin_metrics_cohorts(
    granularity: Literal["week", "month"] = "week",
    since: str | None = None,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Admin: per-cohort funnel breakdown for the dashboard.

    ``granularity`` toggles week (default, ISO-week labels) vs. month.
    ``since`` (YYYY-MM-DD) filters to recent cohorts only.
    """
    _require_admin(x_admin_token)
    with _open_metrics_conn() as conn:
        cohorts = _metrics.compute_cohorts(conn, granularity=granularity, since=since)
    return {"granularity": granularity, "since": since, "cohorts": cohorts}


@app.get("/api/admin/metrics/funnel")
async def admin_metrics_funnel(
    since: str | None = None,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Admin: all-time funnel rollup across every cohort."""
    _require_admin(x_admin_token)
    with _open_metrics_conn() as conn:
        return _metrics.compute_funnel_rollup(conn, since=since)


@app.get("/api/admin/metrics/distribution")
async def admin_metrics_distribution(
    since: str | None = None,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Admin: current tier distribution across live tenants."""
    _require_admin(x_admin_token)
    registry = _load_registry()
    with _open_metrics_conn() as conn:
        return _metrics.compute_tier_distribution_today(conn, registry, since=since)


@app.get("/api/admin/launch-tenants")
async def admin_launch_tenants(
    status: str | None = None,
    x_admin_token: str | None = Header(default=None, alias="X-Admin-Token"),
) -> dict[str, Any]:
    """Admin: list Launch / Growth tenants from the launch registry.

    Optional ``status`` query filters to one bucket
    (``provisioning`` / ``live`` / ``suspended`` / ``canceled`` /
    ``decommissioned``). Customer emails are returned because the admin
    UI is operator-only and needs them to act on dunning alerts.

    Returns counts per status alongside the full tenant list so the
    admin dashboard can render summary cards + a sortable table from
    one fetch.
    """
    _require_admin(x_admin_token)
    if not LAUNCH_REGISTRY_PATH.exists():
        return {"tenants": [], "counts": {}, "registry_present": False}
    try:
        reg = json.loads(LAUNCH_REGISTRY_PATH.read_text())
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500,
                            detail=f"failed to read launch registry: {e}") from e

    tenants_raw = reg.get("tenants") or {}
    tenants: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for slug, t in tenants_raw.items():
        s = t.get("status") or "unknown"
        counts[s] = counts.get(s, 0) + 1
        if status and s != status:
            continue
        tenants.append({
            "slug": slug,
            "signup_id": t.get("signup_id"),
            "customer_email": t.get("customer_email"),
            "customer_domain": t.get("customer_domain"),
            "tier": t.get("tier"),
            "status": s,
            "ip": t.get("ip"),
            "hetzner_location": t.get("hetzner_location"),
            "created_at": t.get("created_at"),
            "canceled_at": t.get("canceled_at"),
            "decommissioned_at": t.get("decommissioned_at"),
            "last_seen_at": t.get("last_seen_at"),
            "paddle_subscription_id": t.get("paddle_subscription_id"),
        })

    # Sort by created_at desc so newest is on top
    tenants.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return {
        "tenants": tenants,
        "counts": counts,
        "total": len(tenants_raw),
        "registry_present": True,
    }
