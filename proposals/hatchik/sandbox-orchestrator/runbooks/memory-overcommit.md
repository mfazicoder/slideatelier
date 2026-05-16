# Memory Overcommit Runbook — Hatchik Sandbox Host

**Audience:** Hatchik operator (founder, ops).
**Applies to:** Hetzner CAX31 (16 vCPU, 32 GB RAM, 160 GB NVMe) hosting ~45 sandbox tenants.
**Status:** Required one-time host setup before tenant onboarding.

---

## 1. Why overcommit?

Each Hatchik sandbox tenant is a docker-compose stack with `mem_limit` set at ~2 GB per
tenant in `docker-compose.yml`. With 45 tenants, the worst-case reservation is:

```
45 tenants x 2 GB = 90 GB requested
```

A CAX31 has 32 GB physical RAM, so requested >> physical. We make this work by relying
on the observed access pattern:

- Each tenant's **warm working set** is closer to ~1 GB, not the 2 GB ceiling.
- At any moment **≤ 70% of tenants are active** (the rest idle on the login page or
  between sessions). Empirically:

```
45 tenants x 1 GB warm x 0.7 active = ~31.5 GB warm-resident
```

That fits in 32 GB physical, *just*. Swap absorbs the residual when the active fraction
spikes above 70% briefly, or when a tenant transiently exceeds its warm baseline.

The £0.30/mo sandbox price assumes this 1.5x density. Without overcommit + swap we'd
have to drop to ~20 tenants per host and the unit economics fall apart.

### Why this is safe

- Linux's default `vm.overcommit_memory=0` heuristic *already* overcommits, but
  refuses large allocations. Mode `1` ("always overcommit") removes the refusal — so
  Postgres/Node startup spikes don't get ENOMEM'd before they touch the pages.
- The actual ceiling on resident memory is enforced by the per-tenant cgroup
  `mem_limit`, not by the kernel overcommit heuristic. We are not asking the kernel
  to magic memory out of nothing — we are telling it not to second-guess our cgroup
  policy.

---

## 2. Sysctl settings

Applied by `scripts/setup_overcommit.sh` into `/etc/sysctl.d/99-hatchik-overcommit.conf`.

| Setting                       | Value | Why                                                                                              |
|-------------------------------|-------|--------------------------------------------------------------------------------------------------|
| `vm.overcommit_memory`        | `1`   | Always allow overcommit. Required so docker-compose `up` doesn't ENOMEM on cold-start spikes.    |
| `vm.swappiness`               | `10`  | Prefer keeping warm pages in RAM. Default 60 swaps too eagerly for our latency budget.           |
| `vm.vfs_cache_pressure`       | `50`  | Mildly favour inode/dentry cache retention. Tenant containers do lots of small-file IO on boot.  |
| `vm.overcommit_ratio`         | n/a   | **Not relevant when `overcommit_memory=1`** — that mode ignores the ratio entirely. Documented here so future ops don't try to "tune" it. |

### Transparent Huge Pages (THP)

THP defaults to `always` on most distros. **Set THP to `madvise` (or `never`).**
Postgres docs explicitly recommend disabling THP — it causes latency spikes when khugepaged
defragments memory under pressure, which is exactly when our sandbox tenants are most
sensitive.

```
echo madvise > /sys/kernel/mm/transparent_hugepage/enabled
```

Persist via a systemd drop-in or `rc.local` if your distro doesn't already. (Not
included in `setup_overcommit.sh` because distros vary — operator should verify
`cat /sys/kernel/mm/transparent_hugepage/enabled` shows `[madvise]` or `[never]`.)

---

## 3. Swap configuration

- **Size:** 16 GB (50% of 32 GB RAM). Large enough to absorb a temporary spike of all
  45 tenants going active at once; small enough that we don't waste NVMe.
- **Location:** `/swapfile` on the root NVMe disk.
- **Mount opts:** `noatime` (don't update access times on the swap inode) and `discard`
  (TRIM on swap-out, keeps NVMe healthy).
- **Permissions:** `600` (root only — `swapon` refuses world-readable swap files).

### Why swap on NVMe specifically

A CAX31's default disk is NVMe (~3 GB/s sequential). Swap on NVMe is acceptable for
this workload — page-in latency is microseconds, not milliseconds.

**Do not run this on a host with rotational disk for swap.** A spinning disk swap
file would convert our 70%-active assumption into 70%-broken: tenants paged in from
HDD would stall for hundreds of ms and users would see request timeouts. If you're
ever migrating to a non-NVMe host, redo the pricing model first.

---

## 4. Per-tenant cgroup limits

The kernel-level overcommit setting is the floor; per-tenant `mem_limit` in
`sandbox-orchestrator/templates/docker-compose.yml` is the ceiling. See that file for
the exact values. Together:

- Kernel: "I won't refuse allocations preemptively."
- cgroup: "But this tenant cannot exceed 2 GB resident."

A misbehaving tenant gets OOM-killed inside its own cgroup; the host stays up and
other tenants are unaffected.

---

## 5. Monitoring

### Spot OOM-kills

```
dmesg -T | grep -i 'killed process'
journalctl -k --since '1 hour ago' | grep -i 'out of memory'
```

A handful of OOM-kills *inside* a tenant cgroup per week is normal — it means
`mem_limit` is doing its job. Host-level OOM-kills (the kernel's global OOM-killer
firing) are **not** normal and should never happen under correct configuration.

### Memory pressure (PSI)

```
cat /proc/pressure/memory
```

Output looks like:

```
some avg10=0.42 avg60=0.30 avg300=0.18 total=12345678
full avg10=0.00 avg60=0.00 avg300=0.00 total=0
```

- `some avg10` — % of last 10s where at least one task was stalled on memory. **Our
  watchdog alerts when this crosses 20.**
- `full avg10` — % of last 10s where *all* tasks were stalled. Anything above 0 here
  sustained is a five-alarm fire; add capacity now.

### Watchdog

The `hatchik-memory-watchdog.timer` runs every 5 min, calls `memory_watchdog.py`,
and writes a marker file at `/var/run/hatchik-memory-pressure` + a journald warning
when `some avg10 > 20`. `journalctl -u hatchik-memory-watchdog.service` shows the
history.

---

## 6. Rollback criteria

Roll back overcommit (revert to `/etc/sysctl.conf.pre-hatchik` and disable swap)
if any of these fire on a steady-state host (not during a known incident):

| Signal                                                  | Threshold to roll back                |
|---------------------------------------------------------|---------------------------------------|
| Host-level OOM-kills (global OOM-killer)                | **any single occurrence**             |
| Tenant-cgroup OOM-kills                                 | > 10 per hour, sustained over 6 hours |
| `some avg10` (PSI memory)                               | > 40 sustained over 1 hour            |
| `full avg10` (PSI memory)                               | > 5 sustained over 15 min             |
| Tenant request p99 latency (from app metrics)           | > 2x baseline for > 30 min            |

To roll back:

```
sudo cp /etc/sysctl.conf.pre-hatchik /etc/sysctl.conf
sudo rm /etc/sysctl.d/99-hatchik-overcommit.conf
sudo sysctl --system
# leave swap on for now — disabling swap under pressure crashes the host
```

Then reduce tenant count on the host (migrate the bottom-quartile-by-activity tenants
to a new CAX31) until pressure drops, then decide whether to keep overcommit off.

---

## 7. Capacity watermark — when to add a second host

We've tested this configuration up to **35 concurrent warm sandboxes** without
sustained pressure. The hard plan:

- **25 warm-concurrent:** comfortable. No action.
- **30 warm-concurrent:** start provisioning the next CAX31 (don't wait for pressure).
- **35 warm-concurrent:** alert fires (watchdog). Cut over the next 10 new tenants to
  the new host.
- **40 warm-concurrent on a single CAX31:** you are past the tested envelope. Pages-on
  duty. Migrate aggressively.

"Warm-concurrent" = tenants with at least one HTTP request in the last 15 min, per
the orchestrator's tenant-activity log. The watchdog does not count this directly —
the operator should pull it from the orchestrator metrics or write a separate timer.

---

## 8. One-time setup procedure

```
sudo bash sandbox-orchestrator/scripts/setup_overcommit.sh
sudo bash sandbox-orchestrator/scripts/check_overcommit.sh   # verify
sudo cp sandbox-orchestrator/systemd/hatchik-memory-watchdog.service /etc/systemd/system/
sudo cp sandbox-orchestrator/systemd/hatchik-memory-watchdog.timer   /etc/systemd/system/
sudo cp sandbox-orchestrator/scripts/memory_watchdog.py /usr/local/bin/hatchik-memory-watchdog
sudo chmod +x /usr/local/bin/hatchik-memory-watchdog
sudo systemctl daemon-reload
sudo systemctl enable --now hatchik-memory-watchdog.timer
sudo systemctl status hatchik-memory-watchdog.timer
```

After this, `check_overcommit.sh` should show `vm.overcommit_memory = 1`, 16 GB swap
active, and PSI memory near zero.
