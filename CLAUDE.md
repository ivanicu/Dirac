# Project: dirac

Repo law lives in `AGENTS.md` (co-equal, loaded by the Codex runtime). This file
holds the rules a Claude session in this repo must follow, and right now it holds
one.

---

## ⚠ ONE DEV SERVER: PORT 1360. DO NOT START ANOTHER.

**Ivan, 2026-08-11, standing until he lifts it.**

```
http://192.168.1.3:1360/     LAN
http://100.78.155.10:1360/   tailnet
http://127.0.0.1:1360/       local
```

Serving `/home/ivan/dirac/build/dirac`, bound to `0.0.0.0` so every device on the
LAN and the tailnet reaches the same build.

**The rules, in force for every agent working in this repo:**

1. **Do not start a new HTTP server.** Not on a different port, not "just for a
   screenshot", not "temporarily". If you need to look at the app, rebuild with
   `npm run build:dirac` and reload 1360 — it serves `build/dirac`, so a rebuild
   is all it takes to see your change.
2. **1360 is a systemd `--user` unit**, not a backgrounded shell process:
   `systemctl --user restart dirac-web` · status with `systemctl --user status dirac-web`.
   It is `enabled` (returns after a reboot) and `Restart=always` (returns after
   a crash — verified by killing it). It serves with `-c-1`, i.e. **caching
   off**: a cached bundle behind a browser cache already cost this project an
   hour of debugging a fix that had in fact landed.
   *It became a unit because a backgrounded `python3 -m http.server` dies with
   the shell that started it. "There is one server" then quietly degrades into
   "there is one server until someone closes a terminal", and the next agent
   starts a second one because the first is gone — which is the very thing this
   rule exists to prevent.*
3. **Do not kill 1360.** Other agents and Ivan's other devices are on it.
4. **A test harness that needs its own copy** (e.g. `scripts/shoot-fields.sh`,
   which appends a driver to a staged copy) must stop its server when it exits.
   The harness already does this; anything new must too.

**Why this exists — it is not tidiness, it cost real work.** Fifteen HTTP servers
were running against this repo at once: four different builds of the app on 8100,
8101, 1342 and 1360, plus eleven staging copies from screenshot runs. Two
consequences, both measured on 2026-08-11:

- **A server from an EARLIER SESSION held port 1344.** The harness started a new
  one, it died with "address already in use" into `/dev/null`, and Chrome
  cheerfully loaded the *old* staged app. Two full rounds of verification
  screenshots were of a previous build while being read as evidence about the new
  one. A stale server is a fallback that hides the primary's death, and it fails
  toward looking correct.
- **Several rounds of diagnosis described code that was not running**, because a
  staging directory kept an old `dirac.js`.

So this is a correctness rule, not a housekeeping one: **more than one server
against one repo means you cannot know which build you are looking at**, and the
failure never announces itself — it hands you a plausible screenshot.

---

## ⚠ THE BACKEND DAEMON DOES NOT RELOAD ITSELF

**After touching anything in `backend/`: `systemctl --user restart dirac-fields`.**
(`dirac-physics` likewise for `backend/physics/`.)

The daemon on `:8901` holds your code in memory. Edit a handler, take a
screenshot, and you have photographed a build that no longer exists on disk —
the same class of error as the stale server above, arriving from the other
direction. Measured on 2026-08-11 by the acceptance test: the running daemon
answered from an OLD handler while the in-process leg used the new one, and the
diff reported it as a *transport disagreement* rather than as staleness, which
sent the reader looking for a bug in the transport. `scripts/acceptance_parity.py`
now flags that case specifically. *(Reported by the 架构升级 session, which
measured it.)*

---

**When a rule needs the port to change**, change it here first, then tell the
other agents; do not fork onto a second port and sort it out later.
