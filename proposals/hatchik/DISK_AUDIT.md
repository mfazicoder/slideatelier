# VPS disk audit

Total: 155G | Used: 148G (96%) | Free: 7.2G | Surveyed: 2026-05-12 11:00 UTC
Host: `root@83.228.247.210` (Infomaniak shared prod, Ubuntu 22.04, kernel 5.15.0-177)

## Headline finding

The disk-full problem is **recurring and growing**, driven by two interacting culprits:

1. **`/var/backups/nextcloud` = 82 GB** — daily tarball backups of the Nextcloud data volume. Each backup is now ~30 GB and growing. The retention is "1 day", but a `find -mtime +1` prune means up to ~3 calendar days are kept (mtime semantics). Today's backup (`data-2026-05-12_0315.tar.gz`, 24 GB) **failed mid-tar with "No space left on device"** — confirming the recurring failure mode.
2. **`coolwsd.uBQE00fIR3/jails` inside `nextcloud-app-1` container's `/tmp` = 21 GB** — Collabora Online Office (built-in collabora app in Nextcloud) leaked jail sandboxes that were never cleaned up. The container's writable overlay layer is now 23 GB (`/var/lib/docker/rootfs/overlayfs/23e6...`). This explains the giant "container size" reported by `docker system df`.

Together those two account for ~103 GB. Almost everything else on disk is structurally necessary (actual Nextcloud user data, Postgres for Supabase, running container images for 6+ services).

## Per-project totals (sorted, biggest first)

| Project | On-disk total | Reclaimable | Risk | Notes |
|---|---|---|---|---|
| **Nextcloud** | ~84 GB | **~80 GB** | low/med | 82 GB of backup tarballs in `/var/backups/nextcloud` + 30 GB volume `nextcloud_nextcloud` (containing 29 GB real user data in `__groupfolders/6`) + 1.9 GB image. The backups are the win. |
| **Nextcloud-app container bloat** | ~23 GB | **~21 GB** | low | Stale Collabora jails in container `/tmp`. Safe to drop — they're scratch. |
| **Supabase stack (slideAtelier + Stackr backend)** | ~8 GB | ~0 GB | high | postgres 3 GB image, storage-api 1.3 GB, supavisor 1.4 GB, realtime 629 MB, kong 496 MB, postgres-meta 505 MB, gotrue 78 MB, postgrest 27 MB, imgproxy 315 MB, nginx-certbot 553 MB. All actively used by running containers. `/opt/supabase` = 70 MB code (negligible). DB volume not visible — supabase-db container has only 61 kB writable layer, so it's using a bind mount or in-image — see "Don't touch". |
| **Stackr** | ~736 MB | ~376 MB | med | `/var/www/stackr` = 720 MB (561 MB app, mostly 376 MB `.git` + 115 MB `public/`; 159 MB built `dist/`). `/opt/stackr-api` = 16 MB. The `.git` is reclaimable if deploys don't need it. |
| **slideAtelier** | ~455 MB | ~0 GB | high | `/opt/slideatelier` = 47 MB (43 MB library thumbnails), `slideatelier:latest` image = 408 MB. All in use. There's a stale duplicate tag `slideatelier:vps-amd64` pointing to the same image ID — costs 0 extra bytes (deduplicated). |
| **ThreadLine** | **not deployed** | — | — | No `/opt/threadline`, no `/var/www/threadline`, no `threadline` container, no `threadline` image. ThreadLine is **not on this VPS**. |
| **sereneintel** | **not deployed** | — | — | No matching directory or container found. **Not on this VPS** either. |
| **Hatchik** | ~56 MB | ~0 MB | high | `/var/www/hatchik` = 112 KB (static), `/opt/hatchik-signup` = 56 MB (mostly the 56 MB Python venv). Just deployed — leave it. |

## Docker detail

`docker system df`:

```
TYPE            TOTAL  ACTIVE  SIZE     RECLAIMABLE
Images          15     14      34.23GB  13.06MB (0%)   ← only "alpine:3" is dangling
Containers      15     14      23.08GB  20.48kB (0%)   ← nextcloud-app-1 is 23GB of that
Local Volumes   6      6       32.20GB  0B (0%)        ← all in use
Build Cache     0      0       0B       0B
```

### Image inventory (all 15 — 14 active, 1 dangling)

| Image | Size | Status |
|---|---|---|
| supabase/postgres:15.8.1.085 | 3.00 GB | active (supabase-db) |
| nextcloud:30-apache | 1.87 GB | active (nextcloud-app-1, nextcloud-cron-1) |
| supabase/supavisor:2.7.4 | 1.44 GB | active (supabase-pooler) |
| supabase/storage-api:v1.48.26 | 1.30 GB | active (stackr-storage) |
| supabase/realtime:v2.76.5 | 629 MB | active (realtime-dev.supabase-realtime) |
| jonasal/nginx-certbot:6.0.1-nginx1.29.5 | 553 MB | active (supabase-nginx, "Created" state — never started) |
| supabase/postgres-meta:v0.96.3 | 505 MB | active (supabase-meta) |
| kong/kong:3.9.1 | 496 MB | active (supabase-kong) |
| mariadb:10.11 | 466 MB | active (nextcloud-db-1) |
| slideatelier:latest | 408 MB | active (slideatelier) |
| slideatelier:vps-amd64 | 408 MB | duplicate tag of `:latest` (same image ID, 0 extra bytes) |
| darthsim/imgproxy:v3.30.1 | 315 MB | active (supabase-imgproxy) |
| supabase/gotrue:v2.186.0 | 78 MB | active (stackr-auth) |
| redis:7-alpine | 61 MB | active (nextcloud-redis-1) |
| postgrest/postgrest:v14.8 | 27 MB | active (stackr-rest) |
| **alpine:3** | **13 MB** | **DANGLING — 0 containers** |

### Container writable-layer sizes (the eye-catchers)

| Container | Writable layer | Status |
|---|---|---|
| **nextcloud-app-1** | **23 GB** | Up 2 days. 21 GB is leaked Collabora jails in `/tmp/coolwsd.uBQE00fIR3/jails/` (197 dirs). The other ~1.4 GB is mostly `appimage_extracted_cbf8a56d8bce1c31b0e51f0e8ad01470` (850 MB). |
| supabase-kong | 115 MB | normal |
| All others | < 1 MB | normal |

### Container JSON logs

| Container | Log size |
|---|---|
| supabase-pooler (supavisor) | **77 MB** |
| nextcloud-app-1 | 30 MB |
| supabase-kong | 5.8 MB |
| slideatelier | 1.5 MB |
| nextcloud-redis-1 | 1.0 MB |
| nextcloud-cron-1 | 584 KB |
| All others | < 500 KB |

Total ~118 MB. No log-rotation is configured for Docker JSON logs.

### Volumes

| Volume | Size | In use by |
|---|---|---|
| **nextcloud_nextcloud** | **31.9 GB** | nextcloud-app-1, nextcloud-cron-1 — **29 GB is real user data** in `__groupfolders/6` (one Nextcloud group folder), 66 MB Nextcloud apps log, ~150 MB per-user homes. |
| nextcloud_db | 293 MB | nextcloud-db-1 (MariaDB) |
| stackr-platform_db-config | 16 KB | supabase-db |
| stackr-platform_nginx_letsencrypt | 11 KB | supabase-nginx |
| stackr-platform_stackr-storage | 0 B | stackr-storage (storage bind to elsewhere) |
| `0ea4e5e1498...` (anonymous) | 28 KB | nginx-certbot bookkeeping |

### Storage architecture note

This Docker uses the **containerd image-store** mode. The 28 GB you see in `/var/lib/docker/rootfs/overlayfs/` and the 30 GB in `/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/` are the **same data** — the former are overlay mountpoints whose `upperdir`/`lowerdir` live in the latter. Counting both double-counts. Real container/image storage on disk: ~30 GB.

## Non-Docker disk

| Path | Size | Notes |
|---|---|---|
| **/var/backups/nextcloud** | **82 GB** | 3 backup tarballs: `data-2026-05-10_0315.tar.gz` (30G), `data-2026-05-11_0315.tar.gz` (30G), `data-2026-05-12_0315.tar.gz` (24G, **partial — last run failed mid-tar**). DB dumps 913 KB each. Cron config: `/etc/cron.d/nextcloud-backup` runs `/opt/nextcloud/backup.sh` daily at 03:15 UTC. Script uses `find -mtime +1`, which by `find` semantics keeps ~2-3 calendar days — explains why three files are present despite "1 day" retention. |
| /var/lib/containerd (real image data) | 30 GB | All active. See above. |
| /var/lib/docker/volumes | 31 GB | Mostly nextcloud_nextcloud (29 GB real user data). |
| /var/www/stackr | 720 MB | 561 MB `app` (376 MB `.git`, 115 MB `public`, 69 MB `src`), 159 MB `dist` |
| /opt/supabase | 70 MB | code only |
| /opt/hatchik-signup | 56 MB | Python venv |
| /opt/slideatelier | 47 MB | mostly library thumbnails (43 MB) |
| /var/log/journal | 177 MB | systemd journal (capped at 253 MB per `journalctl --disk-usage`) |
| /boot | 117 MB | 2 kernels: `5.15.0-176-generic` + `5.15.0-177-generic` (current). One reclaimable. |
| /var/lib/snapd/snaps | 409 MB | 2 disabled snap revisions: core20_2599 (64 MB), lxd_31333 (90 MB), snapd_26382 (49 MB). 203 MB reclaimable. |
| /root | 40 MB | 24 MB `.npm`, 16 MB `.cache` |
| /home/deploy | 4.4 MB | empty-ish |
| /tmp | 96 MB | mostly transient |
| /var/cache | 213 MB | apt caches |
| /var/log (non-journal) | ~32 MB | normal log rotation |

## Recommended cleanup (in order, with exact commands)

### 1. SAFE & FAST — drop the failed/partial backup (~24 GB reclaim)

The 24 GB `data-2026-05-12_0315.tar.gz` is **incomplete** (tar broke with "No space left on device"). It is not a usable backup. Verify with `tar tzf` first; if it errors, delete:

```bash
ls -la /var/backups/nextcloud/data-2026-05-12_0315.tar.gz
tar tzf /var/backups/nextcloud/data-2026-05-12_0315.tar.gz > /dev/null  # expect error
rm /var/backups/nextcloud/data-2026-05-12_0315.tar.gz
rm /var/backups/nextcloud/db-2026-05-12_0315.sql.gz   # the DB dump succeeded but was for a backup pair that didn't complete; the prior day's pair is intact
```

**Why safe:** the file is provably corrupt (tar exited with broken pipe). The DB dump alone without the data tarball is useless.

### 2. SAFE & FAST — drop the older of the two completed backups (~30 GB reclaim)

You currently have completed daily backups for 2026-05-10 and 2026-05-11. Keep yesterday (`05-11`) as your hot rollback target; the day-before is redundant for a 1-day-retention policy.

```bash
ls -la /var/backups/nextcloud/data-2026-05-10_0315.tar.gz   # confirm timestamp & size
rm /var/backups/nextcloud/data-2026-05-10_0315.tar.gz
rm /var/backups/nextcloud/db-2026-05-10_0315.sql.gz
```

**Why safe:** stated retention policy is 1 day; this is an extra copy beyond policy.

**After steps 1+2: ~54 GB reclaimed, disk drops from 96% to ~62%.**

### 3. SAFE — clear Collabora's leaked jails inside nextcloud-app-1 (~21 GB reclaim)

The collabora-online app stores ephemeral document-edit sandboxes under `/tmp/coolwsd.*/jails/` inside the Nextcloud container. They accumulated 197 jail dirs over ~3 weeks (oldest entries dated 2026-04-23). These are throwaway scratch directories and are recreated on demand.

Stop coolwsd inside the container, clear the jails, restart:

```bash
# Inspect first
docker exec nextcloud-app-1 sh -c "du -sh /tmp/coolwsd.*/jails 2>/dev/null; ls /tmp/coolwsd.*/jails | wc -l"

# Find the running coolwsd if any (collabora "Built-in CODE Server" or external)
docker exec nextcloud-app-1 sh -c "pgrep -a coolwsd || true"

# Stop coolwsd cleanly if it's running inside the container:
docker exec nextcloud-app-1 sh -c "pkill -f coolwsd || true; sleep 2"

# Delete the jails (NOT the systemplate, NOT the .pid)
docker exec nextcloud-app-1 sh -c 'rm -rf /tmp/coolwsd.*/jails/*'

# Coolwsd will recreate jails as needed when users open documents.
```

**Why safe:** Collabora jails are scratch — coolwsd recreates them on each document open. The risk is one editing session in flight gets a "reload document" message. Schedule for off-hours if any users are active.

**Better long-term fix:** disable the in-Nextcloud "Built-in CODE Server" app if it isn't actively used, or move Collabora to its own container with a `tmpfs` mount for `/tmp` so leaks don't persist on disk.

### 4. MODERATELY SAFE — Docker housekeeping (~13 MB now; protects against drift)

```bash
docker image prune -f                              # drops the dangling alpine:3 (~13 MB)
docker rm supabase-nginx                           # status="Created", never started (20.5 KB)
# Optional: drop duplicate tag (releases nothing extra, but cleans inventory)
docker rmi slideatelier:vps-amd64
```

**Why safe:** alpine:3 has 0 containers; supabase-nginx was never started; the `vps-amd64` tag is a duplicate ID.

### 5. MODERATELY SAFE — rotate Docker JSON logs (~115 MB reclaim, prevents growth)

Configure log limits in `/etc/docker/daemon.json` (or whichever config the daemon uses):

```json
{
  "log-driver": "json-file",
  "log-opts": { "max-size": "20m", "max-file": "3" }
}
```

Then `systemctl restart docker` (will briefly bounce all containers — schedule).

Or, immediately truncate the big ones without a restart:

```bash
truncate -s 0 /var/lib/docker/containers/4848548550c548c3346f281dcd63bfa94b6a5c6244bb220b27f82806db113247/4848548550c548c3346f281dcd63bfa94b6a5c6244bb220b27f82806db113247-json.log   # supabase-pooler 77M
truncate -s 0 /var/lib/docker/containers/23e67428c1044f384aca37e823c770788faacd9fd1345688f0734dd6c8ad8e38/23e67428c1044f384aca37e823c770788faacd9fd1345688f0734dd6c8ad8e38-json.log   # nextcloud-app-1 30M
```

**Why safe:** Docker JSON logs are not load-bearing for the apps; `truncate -s 0` is safe while containers run (Docker re-opens the file).

### 6. REQUIRES CARE — purge old kernel (~30 MB reclaim)

`5.15.0-176-generic` is not the running kernel (`uname -r` = 5.15.0-177-generic). Remove only if `-177` boots cleanly (it has been running 4 days):

```bash
apt list --installed 2>/dev/null | grep linux-image
apt-get purge linux-image-5.15.0-176-generic linux-headers-5.15.0-176-generic linux-modules-5.15.0-176-generic
update-grub
```

**Risk:** if `-177` later panics on boot, you've lost your rollback kernel. Worth it only when truly tight on disk; after steps 1-3 you don't need this.

### 7. REQUIRES CARE — snap revisions (~200 MB reclaim)

Disabled snap revisions are kept by snapd for rollback:

```bash
snap list --all                                            # confirm what's "disabled"
snap remove --revision=2599 core20
snap remove --revision=31333 lxd
snap remove --revision=26382 snapd
```

**Risk:** loss of single-revision rollback for those snaps. On a server VPS with no UI use this is low risk, but the size is small relative to other wins.

### 8. OPTIONAL — Stackr `.git` (~376 MB reclaim)

`/var/www/stackr/app/.git` is 376 MB. If the production deploy doesn't need git history (most don't), you can replace with a shallow clone or a tarball'd snapshot:

```bash
# Confirm prod doesn't read .git (e.g., for sha-in-banner) before doing this
du -sh /var/www/stackr/app/.git
# Option A: keep history but pack it
git -C /var/www/stackr/app gc --aggressive --prune=now
# Option B: drop git entirely
rm -rf /var/www/stackr/app/.git
```

**Risk:** if the deploy or CI relies on the `.git` directory for sha lookup, you'll break it. Verify first.

## Don't touch

| Item | Why |
|---|---|
| `/var/lib/docker/volumes/nextcloud_nextcloud/_data/data/__groupfolders/6` (27 GB) | Real user data — a Nextcloud group folder. Largest single data store on the box. |
| `/var/lib/containerd/io.containerd.snapshotter.v1.overlayfs/snapshots/` (30 GB) | Actively-mounted overlay layers for the 14 running containers. Deleting any breaks a service. Do not `docker prune -a` either — `RECLAIMABLE` is reported as 13 MB, meaning every image is in use. |
| All 14 running containers' images | Confirmed active by `docker container ls`. No stale Stackr-era images, no orphans. |
| `/opt/supabase/volumes/db` (69 MB) — config seed | Supabase init/seed scripts; small but load-bearing for re-bootstrap. |
| `/opt/slideatelier/library/thumbnails` (43 MB) | Production asset library; served at runtime. |
| `/boot/vmlinuz-5.15.0-177-generic` and matching initrd | Currently-running kernel. |

**Especially do NOT run `docker system prune -a --volumes`** — it would try to wipe `nextcloud_nextcloud` (the 31 GB group-folder data) because the cron container `nextcloud-cron-1` is marginal in detection logic on some Docker versions. Stick to targeted commands above.

## Long-term recommendation

The single biggest fix is the Nextcloud backup pipeline. Three changes, in priority order:

1. **Stream backups off-box, don't store them locally.** A 30 GB tar copied to local disk every day on a 155 GB volume that also hosts 6 other services has no margin. Rsync/restic to S3, Backblaze B2, Hetzner Storage Box, or `rclone copy` to a Nextcloud-as-target. Then keep only 1 day locally, or zero. Estimated savings: 50-80 GB.

2. **Fix the retention math in `/opt/nextcloud/backup.sh`.** `find -mtime +1` keeps roughly 2 calendar days. If you want strictly "yesterday only", use `-mmin +1440` after computing exact ages, or sort by name and `tail -n +3 | xargs rm`. Also: the *pre-backup* prune doesn't catch a partial leftover from a previous failed run — add a `rm -f $BACKUP_DIR/data-$STAMP.tar.gz` at start.

3. **Set Docker JSON log limits globally** in `/etc/docker/daemon.json` (see step 5). Otherwise supabase-pooler will hit hundreds of MB in a few weeks.

4. **Set a Collabora cleanup cron** if Collabora stays in-Nextcloud:
   ```cron
   30 4 * * * docker exec nextcloud-app-1 sh -c 'find /tmp/coolwsd.*/jails -maxdepth 1 -mindepth 1 -type d -mmin +60 -exec rm -rf {} +' >> /var/log/coolwsd-prune.log 2>&1
   ```
   Or, ideally, run Collabora in its own container with `--tmpfs /tmp` so leaks can't persist.

5. **Add a disk-usage monitor.** A weekly cron emailing `df -h /` when > 75% used would have caught this two weeks ago.

6. **Capacity-wise**, this 155 GB box is hosting Nextcloud (30 GB of group data and growing), Supabase, Stackr web+api, slideAtelier, Hatchik signup, plus dev/build space. If Nextcloud usage continues to grow at all, plan to either move it to its own VPS or upsize. The headline memory says "shared prod NOT fresh — slideAtelier deploy needs its own VPS or co-tenancy redesign" — that recommendation applies equally to Nextcloud now.
