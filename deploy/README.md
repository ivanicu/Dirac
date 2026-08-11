# deploy/ — dirac runtime supervision

Everything here exists because of one fact, verified live before writing any
of this: both backend daemons (fields on :8901, physics on :8902) and the
static web server (:1338) are today started by hand in the foreground and,
the moment that shell exits, get reparented to PID 1 with **zero
supervision**. If one dies, nobody restarts it, and the only symptom a human
sees is "backend offline" in the UI. Verified live (`ps -o pid,ppid`, all
three): PPID = 1 for all three processes.

This directory does not fix that by itself — it documents the fix
(`deploy/systemd/*.service`) and gives you a day-to-day tool for the same
three services (`bin/dev`) plus a safe way to reclaim stale compute
(`bin/dirac-sweep`).

> **STATUS 2026-08-11 — INSTALLED AND RUNNING.** `dirac-fields.service` (:8901)
> and `dirac-web.service` (:1338) are enabled under `systemctl --user`, with
> linger ON, so both start at boot without a login. Supervision was PROVEN, not
> assumed: `kill -9` on the fields daemon's MainPID and it returned with a new
> pid inside 8 s, `/health` 200. `dirac-physics.service` (:8902) is installed but
> NOT started — that port is held by a hand-run daemon belonging to another
> session, and a second one cannot bind it. Hand it over with
> `systemctl --user enable --now dirac-physics.service` once the manual process
> is stopped.
>
> `dirac-backend.service` was SPLIT into `dirac-fields` + `dirac-physics` (the
> merged file is in `_archive/`). Its own header had already argued for the
> split; the port conflict above is what made it concrete, because a merged unit
> restart-loops BOTH daemons when one port is taken — taking down a working
> fields daemon in order to fail at starting a physics one.
Installing them is a deliberate step — see below.

## What's here

| path | what |
|---|---|
| `bin/dev` | up / down / status / build / logs for fields, physics, web. Never touches a daemon it didn't start itself — another session's daemon survives `bin/dev down`. |
| `bin/dirac-sweep` | the admin DELETE boundary for stale `app.field_cube` rows + orphaned `app.blob` bytes. CLI, not HTTP — see "why a CLI" below. |
| `deploy/systemd/dirac-fields.service` | supervises the fields daemon (:8901). INSTALLED + RUNNING. |
| `deploy/systemd/dirac-physics.service` | supervises the physics daemon (:8902). Installed, NOT started — port held by a hand-run process. |
| `deploy/systemd/_archive/dirac-backend.service.superseded` | the merged both-daemons unit these two replaced. Kept, not deleted: its header holds the measured memory-ceiling derivation both inherit. |
| `deploy/systemd/dirac-web.service` | supervises the static web server (:1338). |

## Verified-live facts (re-check before trusting an older doc)

Existing docs disagree with each other and with the running system — see
"the host-claim mess" below. These are the facts this deploy/ was built
against, each checked directly rather than read off a comment:

| service | port | actual bind (checked with `ss -ltnp`) | start command |
|---|---|---|---|
| fields daemon | 8901 | `0.0.0.0` | `backend/env/bin/python backend/field_server.py` |
| physics daemon | 8902 | `0.0.0.0` | `backend/env/bin/python backend/physics/server.py` |
| static web | 1338 | `0.0.0.0` | `node_modules/.bin/http-server build/dirac -p 1338 -g -c-1` |

All three bind every interface, on purpose — `backend/physics/README.md`
says why: "Bound to all interfaces because Ivan drives this from a Mac on
the LAN, and a loopback-only daemon simply reports 'offline' there." The
`-c-1` on the web server **disables caching**; dropping it is what cost an
hour today to a stale cached bundle after a rebuild. `-g` enables gzip.

Build: `npm run build:dirac` — **not** the bare `node ./scripts/build.mjs -a
dirac --prd`. Verified in `scripts/build.mjs`: it only ever `mkdirSync`s and
`copyFile`s from its own asset manifest, never `rmSync`s the output
directory and never copies `assets/rdkit`. `npm run build:dirac` is that
same command *plus* an explicit `fs.cpSync(...assets/rdkit...)` step. On a
from-scratch checkout the bare command produces a bundle whose
`index.html` requests `./assets/rdkit/RDKit_minimal.js` and gets a 404 —
silently broken in the browser console, not "backend offline" but the same
species of failure this whole directory exists to stop. `bin/dev` always
runs the full `npm run build:dirac`.

### The host-claim mess (why this file exists)

The startup command is documented with an explicit host:port comment in
exactly four places. Three of the four are wrong:

| file:line | claim | actual | verdict |
|---|---|---|---|
| `backend/field_server.py:20` (its own header) | `127.0.0.1:8901` | `0.0.0.0` | **wrong** |
| `backend/README.md:11` | `127.0.0.1:8901` | `0.0.0.0` | **wrong** |
| `backend/physics/server.py:4` (its own header) | `127.0.0.1:8902` | `0.0.0.0` | **wrong** |
| `backend/physics/README.md:9` | `0.0.0.0:8902` | `0.0.0.0` | correct |

Both python files' own top-of-file "Run:" comments are wrong about their own
bind address — `field_server.py` line 1119 literally binds `'0.0.0.0'` and
line 1117's own inline comment says *"0.0.0.0: the app is used from other
machines on the LAN (Ivan's Mac)"*, twenty lines below a header comment
that still says `127.0.0.1`. Same story in `physics/server.py`: its `HOST`
default (line 58) is `0.0.0.0`, its own README (one directory entry away)
correctly says `0.0.0.0:8902`, and its own file header still says
`127.0.0.1:8902`. `README.md`, `ARCHITECTURE.md`, `AGENTS.md`, and
`CHANGELOG.md` also mention these ports; none of that is touched here —
fixing them is out of scope for this task (`README.md`, `AGENTS.md`,
`backend/README.md` are explicitly not owned by this change). This table is
the corrected reference; treat it as current, not the four files above.

## `bin/dev`

```
bin/dev up       # PG check -> build if stale -> start what isn't listening -> summary table with real /health checks
bin/dev status   # listening?/pid/RSS per service, + fields' /health (db_cache, scf_cached, rss_mb)
bin/dev down     # stops ONLY services bin/dev itself started (tracked in /tmp/dirac-dev/*.pid)
bin/dev build    # npm run build:dirac, unconditionally
bin/dev logs     # tail of /tmp/dirac-dev/logs/*.log for anything bin/dev started
```

Idempotent by construction: `up` checks each port before starting anything,
so a second `up` never double-starts. `down` only stops a service if the pid
recorded in `/tmp/dirac-dev/<name>.pid` is *currently* the process listening
on that service's port — a service running from an earlier hand-started
session (or another session's dev loop) is left alone and reported as such,
never pattern-killed.

This script is the everyday tool. It is **not** a substitute for the systemd
units below — a process `bin/dev` starts is still just as unsupervised
after the script exits as today's hand-started ones; it only tracks enough
state to manage what it started during this shell's lifetime.

## `bin/dirac-sweep`

The admin DELETE boundary for `app.field_cube` / `app.blob` lives in this
CLI, not behind an HTTP route. Verified live: neither `field_server.py` nor
`physics/server.py` currently exposes a DELETE endpoint at all — this is a
boundary decided *before* one gets added, not a patch for one that exists.
The reasoning: an unauthenticated LAN endpoint that can delete hours of SCF
compute is not an acceptable shape for that capability, and shell access on
this box already **is** the auth boundary that matters — putting the same
capability behind HTTP would only widen who can trigger it, for no gain.

```
bin/dirac-sweep              # dry run (default): reports, deletes nothing
bin/dirac-sweep --dry-run    # same, explicit
bin/dirac-sweep --apply      # deletes stale field_cube rows + now-orphaned blobs
```

What "stale" means, verified against `backend/db/migrations/006_producer_identity.sql`
before writing any SQL: a `field_cube` row whose `producer_id` points at a
`meta.producer` row with `superseded_at IS NOT NULL` — exactly the rows
`app.v_field_cube_stale` aggregates. A row referenced by `app.job`
(`job_field_cube_id_fkey`, `ON DELETE NO ACTION`) is **skipped**, even under
`--apply`, and reported as skipped rather than aborting the whole run. A
blob is deleted only if, after the field_cube deletion above, **no**
remaining field_cube row (current-producer, or a stale one kept because a
job still points at it) references it — that check is re-run live, not
computed once up front, so it can't go stale mid-operation.

## Installing the systemd units (Ivan's call — not done by this commit)

These are **user** units (they run as your own login session, same as the
existing `dirac-sync.timer` in this same directory), not system units — no
`sudo` involved in installing them.

```bash
# from the repo root
systemctl --user link "$PWD/deploy/systemd/dirac-fields.service"
systemctl --user link "$PWD/deploy/systemd/dirac-physics.service"   # :8902 — see STATUS above before starting
systemctl --user link "$PWD/deploy/systemd/dirac-web.service"
systemctl --user daemon-reload
systemctl --user enable --now dirac-fields.service
systemctl --user enable --now dirac-web.service
```

`link` (rather than copying the file into `~/.config/systemd/user/`) keeps
this repo as the single source of truth for the unit file — edit it here,
`daemon-reload`, done. Before enabling, kill any hand-started copies first
(`bin/dev down`, or `kill` the PPID=1 orphans directly) so the daemon the
unit starts isn't fighting an existing one for the port.

Check it worked:
```bash
systemctl --user status dirac-fields.service dirac-web.service
# and the one nobody checks until after a reboot:
loginctl show-user "$USER" -p Linger --value    # must be "yes"
journalctl --user -u dirac-fields.service -f
# NB: a journalctl -u against a unit name that does not exist prints NOTHING
# and exits 0 — silence there is not "no errors", it is "no such unit".
```

### Uninstalling

```bash
systemctl --user disable --now dirac-fields.service dirac-physics.service dirac-web.service
systemctl --user daemon-reload
# the link target lives in this repo; nothing to delete outside it.
# to fully remove the symlinks systemd creates in ~/.config/systemd/user/:
systemctl --user link --now 2>/dev/null; rm -f ~/.config/systemd/user/dirac-fields.service ~/.config/systemd/user/dirac-physics.service ~/.config/systemd/user/dirac-web.service
systemctl --user daemon-reload
```

### Why the memory numbers are what they are

Full derivation is in the comment block inside
`deploy/systemd/dirac-fields.service` (it's the load-bearing copy — this is
a summary, not a second source of truth). Headline: `MemoryHigh=6G`,
`MemoryMax=8G`. Measured inputs:

- pyscf's default `max_memory`, checked live in this repo's own env
  (`backend/env/bin/python3 -c "from pyscf import gto,scf;
  print(scf.RHF(gto.M()).max_memory)"`) → **4000 MB**. Neither daemon
  overrides it (grepped both files for `max_memory` — absent in both), and
  `backend/physics/mep_surface.py` creates its own RHF/UHF objects too, so
  this budget applies to *both* daemons in the unit, not just fields.
- Idle baseline RSS, measured live with both daemons already running:
  fields ≈ 160 MB, physics ≈ **1.05-1.08 GB**. Physics is not a typo — a
  Python process that has ever made one large numpy/pyscf allocation tends
  to keep that RSS afterward (glibc malloc arenas aren't returned to the
  OS), so idle RSS on this box is not a safe proxy for "doing nothing."
- A 2 GB cgroup cap (called out explicitly as the thing to avoid) leaves
  under 1 GB of headroom over the *measured idle baseline alone*, before
  either daemon is asked to do a single SCF — it would OOM-kill mid-field
  on the first real request.
- `MemoryHigh=6G` gives baseline (~1.3G combined) + one full 4000 MB pyscf
  budget, with >2.5G of soft-throttle headroom before the hard ceiling.
  `MemoryMax=8G` covers that same baseline plus most of a *second*
  concurrent SCF (both daemons use `ThreadingHTTPServer`, so two
  simultaneous requests is a real case, not a hypothetical). 8G is ~14% of
  this box's 59 GB RAM (34G free at measurement time) — a small, safe
  reservation.

### Known limitation, stated rather than hidden

RESOLVED 2026-08-11 — split into two units; kept because the reasoning is
the reason the split happened. `dirac-backend.service` ran both python
daemons under one unit via a
`wait -n`-based bash wrapper (see the unit file's own top comment for the
full reasoning) because this task granted exactly one backend unit path.
Consequence: if physics crashes, fields restarts too (and vice versa) — a
worse blast radius than the two independent units
`backend/physics/README.md`'s own "separate daemon on purpose" design
implies would be correct. If that shared-fate restart becomes a real
problem, the fix is a second unit file
(`deploy/systemd/dirac-physics.service`, splitting the current
`dirac-backend.service` into fields-only) — DONE, see STATUS at the top —
which was a small change but is
explicitly out of scope for what was authorized here.

## `dirac-sync.timer` — read this before you rely on `main` being what you think it is

**Not owned by this change — do not modify it.** Documented here because it
runs every 60 seconds against this same checkout and has already reset
local history out from under a running session twice in one day, and anyone
running `bin/dev` or `bin/dirac-sweep` needs to know it's there.

Unit: `~/.config/systemd/user/dirac-sync.timer` (`OnUnitActiveSec=60`) runs
`~/.config/systemd/user/dirac-sync.service`, which execs
`~/.local/bin/dirac-sync`. Read directly from that script, precisely (an
earlier framing of "reset --hard + push, on divergence" is close but wrong
in a way that matters — the two actions are in different branches, not
simultaneous):

- Only acts when `HEAD` is `main`. On any other branch or detached HEAD:
  no-op.
- `git fetch origin main`. If local `HEAD` already equals `origin/main`:
  no-op.
- **If local is behind** (a strict ancestor of remote): fast-forward merge
  only (`git merge --ff-only`). If the worktree has uncommitted changes
  that collide with incoming files, this fails harmlessly and logs
  `FF-BLOCKED` — nothing is lost, nothing advances.
- **If local is ahead** (remote is a strict ancestor of local): `git push
  origin main`, so GitHub stays canonical and other machines can pick it
  up.
- **If the two have diverged** (neither is an ancestor of the other): the
  *only* branch where `git reset --hard origin/main` happens. Before that
  reset, local's current commit is saved to a new branch
  `backup/diverged-<timestamp>`, and any uncommitted changes are
  `git stash push -u`. Remote wins. Local commits are not destroyed — they
  are moved somewhere you have to know to look.

Verified live, right now, in `.git/sync.log`: two `DIVERGED` events on
2026-08-10 (`17:57:39` and `19:28:43`), and both backup branches exist —
`backup/diverged-20260810-175739`, `backup/diverged-20260810-192843` — plus
one stash entry still sitting in `git stash list`. Nothing from those two
events is gone, but neither is it on `main`, and nothing about this timer
tells you it happened unless you go read the log or `git branch -a`.

**Practical consequence for anyone deploying from this checkout:** if you
commit here and don't push within about a minute while someone else's push
lands first, your commits will silently move to a `backup/diverged-*`
branch and stash entry, and `main` will jump to whatever the remote had.
Check `git branch --list 'backup/diverged-*'` and `git stash list` before
concluding work was lost — it almost certainly is not lost, just relocated.
