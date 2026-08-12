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
2. **If 1360 is down**, restart it *on 1360*:
   `cd /home/ivan/dirac/build/dirac && python3 -m http.server 1360 --bind 0.0.0.0 &`
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

**When a rule needs the port to change**, change it here first, then tell the
other agents; do not fork onto a second port and sort it out later.
