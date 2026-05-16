#!/usr/bin/env python3
"""
memory_watchdog.py — preventive memory-pressure alerting for Hatchik sandbox hosts.

Called by hatchik-memory-watchdog.timer every 5 minutes. Reads
/proc/pressure/memory, compares the "some" line's avg10 against a threshold, and:

  - if avg10 <= threshold: clears the marker file (if present), exits 0 quietly.
  - if avg10 >  threshold: writes a marker file at /var/run/hatchik-memory-pressure
    with a timestamp + the reading, and emits a journald WARNING via syslog.

Idempotent: rerunning under the same pressure state is a no-op (marker rewritten
with current timestamp, but contents are stable in shape). Failure modes
(missing PSI, malformed pressure line) log a NOTICE and exit 0 — we never want
the watchdog itself to flap a service-failed alert.

Python stdlib only.
"""

from __future__ import annotations

import os
import re
import sys
import syslog
import datetime
from typing import Optional

PRESSURE_PATH = "/proc/pressure/memory"
MARKER_PATH = "/var/run/hatchik-memory-pressure"
# Threshold chosen from the runbook: avg10 > 20 on "some" means at least one
# task spent >20% of the last 10s stalled on memory. That's the preventive
# trip-wire well below "full" stalls or OOM, giving the operator time to add
# capacity. See runbooks/memory-overcommit.md section 5/6.
THRESHOLD_SOME_AVG10 = 20.0

# avg10=<float>  (the parser is forgiving about ordering of fields)
_FIELD_RE = re.compile(r"(\w+)=([\d.]+)")


def log(level: int, msg: str) -> None:
    """Write to journald via syslog + stderr so manual `systemctl start` shows it."""
    syslog.syslog(level, msg)
    sys.stderr.write(msg + "\n")


def read_some_avg10(path: str = PRESSURE_PATH) -> Optional[float]:
    """Return avg10 for the 'some' line of /proc/pressure/memory, or None."""
    try:
        with open(path, "r", encoding="ascii") as fh:
            for line in fh:
                if not line.startswith("some "):
                    continue
                fields = dict(_FIELD_RE.findall(line))
                if "avg10" not in fields:
                    return None
                return float(fields["avg10"])
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None
    return None


def write_marker(avg10: float) -> None:
    """Write the alert marker. Best-effort — failure logs but doesn't raise."""
    payload = (
        "hatchik-memory-pressure ALERT\n"
        f"timestamp_utc={datetime.datetime.now(datetime.timezone.utc).isoformat()}\n"
        f"some_avg10={avg10:.2f}\n"
        f"threshold={THRESHOLD_SOME_AVG10:.2f}\n"
        "see: sandbox-orchestrator/runbooks/memory-overcommit.md\n"
    )
    tmp = MARKER_PATH + ".tmp"
    try:
        with open(tmp, "w", encoding="ascii") as fh:
            fh.write(payload)
        os.replace(tmp, MARKER_PATH)
    except OSError as exc:
        log(syslog.LOG_ERR, f"hatchik-watchdog: failed to write marker: {exc}")


def clear_marker() -> None:
    """Remove the alert marker if it exists. No-op otherwise."""
    try:
        os.unlink(MARKER_PATH)
    except FileNotFoundError:
        pass
    except OSError as exc:
        log(syslog.LOG_WARNING, f"hatchik-watchdog: failed to clear marker: {exc}")


def main() -> int:
    syslog.openlog(ident="hatchik-watchdog", logoption=syslog.LOG_PID,
                   facility=syslog.LOG_DAEMON)
    avg10 = read_some_avg10()

    if avg10 is None:
        log(syslog.LOG_NOTICE,
            f"hatchik-watchdog: could not read {PRESSURE_PATH}; skipping tick")
        return 0

    if avg10 > THRESHOLD_SOME_AVG10:
        log(syslog.LOG_WARNING,
            f"hatchik-watchdog: memory pressure some.avg10={avg10:.2f} "
            f"> threshold {THRESHOLD_SOME_AVG10:.2f} — alerting (marker at "
            f"{MARKER_PATH})")
        write_marker(avg10)
    else:
        # Quiet success: log at DEBUG so journal isn't spammed every 5 min.
        log(syslog.LOG_DEBUG,
            f"hatchik-watchdog: ok some.avg10={avg10:.2f}")
        clear_marker()

    return 0


if __name__ == "__main__":
    sys.exit(main())
