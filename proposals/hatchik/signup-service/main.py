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
log = logging.getLogger("loftik-signup")

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
    """Confirm-receipt email to the customer. Founder follows up personally."""
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
    # Paddle returns amounts as minor-unit strings (e.g. "7900" = 79.00 GBP).
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
                status, country_code, city, asn
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
            """,
            (
                created_at, str(req.email), req.first_name, req.product_name,
                req.description, req.tier, req.region, req.domain_choice,
                ip, user_agent, req.github_username or None,
                geo["country_code"] or None, geo["city"] or None, geo["asn"] or None,
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
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO login_tokens (token, email, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, email, created_at.isoformat(), expires_at.isoformat()),
        )
        conn.commit()

    link = f"https://hatchik.com/api/account/auth?token={token}"
    await _send_login_email(email, link)
    log.info("login link emailed to %s", email)
    return {"ok": True, "message": "If that email matches an active Hatchik account, we've sent a sign-in link."}


async def _send_login_email(email: str, link: str) -> None:
    if not RESEND_API_KEY:
        log.warning("RESEND_API_KEY not set — skipping login email to %s", email)
        return
    text = f"""Hi,

Click the link below to sign in to your Hatchik account. It's
single-use and expires in {LOGIN_TOKEN_TTL_MINUTES} minutes.

{link}

If you didn't ask for this, ignore this email.

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
<p style="margin:0 0 16px 0;color:#555;font-size:14px;">If you didn&rsquo;t ask for this, ignore this email.</p>
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


@app.post("/api/account/logout", status_code=204)
async def logout(
    response: Response,
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
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
) -> dict[str, Any]:
    session = _resolve_session(hatchik_session)
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
) -> dict[str, Any]:
    session = _resolve_session(hatchik_session)
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


@app.get("/api/account/upgrade")
async def upgrade_info(
    hatchik_session: str | None = Cookie(default=None, alias=SESSION_COOKIE_NAME),
) -> dict[str, Any]:
    """Return upgrade info — Paddle checkout URL if configured, else 'coming soon'."""
    session = _resolve_session(hatchik_session)
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
) -> dict[str, Any]:
    """Return a Paddle customer-portal URL for the signed-in customer.

    Only customers with at least one successful Launch-tier payment have
    a Paddle customer_id. Until then, this returns 404 and the UI hides
    the billing tab.
    """
    session = _resolve_session(hatchik_session)
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
        # TODO(metrics): when Paddle launches, resolve customer_email → signup_id
        # and insert (signup_id, 'sandbox', 'launch', now, event_id, 'paddle webhook')
        # into tier_transitions. Similarly emit ('launch','growth') for
        # growth-price subscription items. See AGENT_METRICS_REPORT.md.

    elif event_type == "subscription.updated":
        log.info(
            "Paddle subscription.updated: subscription=%s customer=%s status=%s next_billed_at=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("status"),
            data_object.get("next_billed_at"),
        )

    elif event_type == "subscription.canceled":
        log.warning(
            "Paddle subscription.canceled (churn): subscription=%s customer=%s canceled_at=%s",
            data_object.get("id"),
            data_object.get("customer_id"),
            data_object.get("canceled_at"),
        )

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
