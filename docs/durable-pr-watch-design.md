# Durable PR watch — design

Status: implemented
Date: 2026-08-22
Supersedes: the `--await` / deadline-park behaviour of `epic/scripts/pr_watch.py`

## Problem

The epic driver stalls waiting on PR reviews. Six causes, all in the wait path.

1. **Bot reviews are invisible.** `snapshot()` skips `COMMENTED` reviews
   (`pr_watch.py:58`). CodeRabbit and Copilot almost always submit `COMMENTED`
   plus inline threads, never `APPROVED` / `CHANGES_REQUESTED`. The documented
   incantation `--await checks,coderabbitai`
   (`references/github-graphql.md:411`) therefore waits the full deadline and
   parks the child even though the review landed.
2. **The complement is a false positive.** `settled_event()` treats
   `threads_unresolved == 0` as settled, and `"NONE"` (empty check rollup) is in
   `_SETTLED_STATES`. Immediately after PR open — before any bot posts and
   before CI registers — the watch returns `settled, initial: true`. The
   watcher either fires instantly-wrong or never fires.
3. **The wait cannot physically complete.** `main()` blocks with
   `--deadline 3600`, but a foreground tool call is capped far below that. The
   process is killed mid-flight with no JSON on stdout and the driver has no
   defined handling for that outcome. This is the literal stall.
4. **Backoff is neither exponential nor jittered.** `backoff()` is a 15/30/60
   staircase: roughly 120 `gh` calls per hour per child, with every parallel
   wave member polling in lockstep. Nothing honours `Retry-After`.
5. **No durability.** Watch state lives only in process memory. Session ends,
   watch is gone, with no record it existed. SKILL.md step 5 says "reschedule
   via `ScheduleWakeup` ... otherwise keep an in-session loop" with no cadence,
   no persistence, and no stop semantics.
6. **The terminal answer to a slow reviewer is to give up.** Deadline → park.

## Decisions

| Question | Decision |
|---|---|
| Durability scope | Within a long-running session. No cron, no daemon — keeps the plugin portable across Claude, Codex, and Kimi. |
| Wake predicate | Any observable PR activity. The watcher is a dumb change-detector; `mergeability.py` remains the sole authority on what is actionable. |
| Termination | Never on time alone. Ends only on PR closed/merged, explicit stop, or run termination. |
| Mechanism | Tick model. `pr_watch.py` never sleeps; each invocation is one cheap tick. |

## Script contract

`snapshot()` becomes `fingerprint()`: a digest over head SHA, the check rollup,
**all** reviews (including `COMMENTED`), review threads, and PR comment count.
Per-facet sub-digests are retained so an event can name which facet moved.

`settled_event()`, `diff_event()`, `_SETTLED_STATES` and the entire `--await`
key vocabulary are deleted. The script no longer judges gates, so the
divergence documented at `references/github-graphql.md:420` (`checks`
aggregating every rollup entry rather than the ruleset's required ones) stops
mattering and its note is removed with the section.

One invocation: load cursor, fetch, compare, print, exit.

| Exit | Event | Driver action |
|---|---|---|
| `0` | `{"event":"activity","changed":["reviews","threads"],"head":...}` | Re-run `mergeability.py`, act |
| `0` | `{"event":"pr-closed","state":"MERGED"}` | Stop watching |
| `1` | `{"event":"waiting","next_tick_in_s":283,"quiet_s":1240}` | Schedule the next tick |
| `2` | `{"event":"error","detail":...}` | Hard failure |

This matches the exit-code convention already documented in `README.md:95`
(`0` ok, `1` not-yet-satisfied, `2` hard error). No `time.sleep()` remains in
the module; `main()` stays the thin impure shell the other scripts use.

### Backoff

`delay = min(WATCH_FLOOR_S * WATCH_MULT ** step, WATCH_CEIL_S)`, jittered by
±20%. With the defaults below: 15, 27, 49, 87, 157, 283, 510, 900 — reaching
the ceiling in about eight ticks, roughly 35 minutes.

Jitter desynchronises parallel wave members, removing the current thundering
herd.

The step advances by one on every quiet tick. It returns to 0 only on a real
head change — chat noise on a busy PR must not pin the watch at the fast tier.

- `--reset-backoff` — fresh arm (e.g. straight after a push). Forces step 0.
- `--resume-backoff` — the driver woke, found nothing actionable, and is
  re-arming. Asserts the default: continue from the stored step.

`gh` errors advance a **separate** error backoff that honours `Retry-After` and
`x-ratelimit-reset` when they can be parsed from stderr. Exit `2` only after
eight consecutive failures.

`next_tick_in_s` is a **request, not a command**. The driver clamps it to its
own scheduler's range: Claude's `ScheduleWakeup` has a 60 s floor, so early
15 s and 27 s ticks clamp up; Codex and Kimi in-session loops honour them
as-is. The script stays harness-agnostic.

### Cursor

`~/.cache/epic/watch/<owner>__<name>__<pr>.json`, overridable with
`--state-dir` or `EPIC_WATCH_DIR`. It holds exactly five fields: last
fingerprint, backoff step, consecutive error count, the timestamp of the last
observed activity, and the facets that moved at that timestamp. The last two
are what `quiet_s` and `status.py`'s silence report
(`waiting 47m on PR #12, last activity: checks`) are derived from.

**Missing or corrupt means start fresh at step 0.** Losing the cursor costs one
wasted fast-tier poll and nothing else. That is what keeps this compatible with
SKILL.md's HARD stateless-recovery invariant — the cursor is an optimisation,
never authoritative for gates.

### Stop semantics

Three ways a watch ends, none of them a timeout:

- the PR is no longer `OPEN` (the watch self-terminates with `pr-closed`);
- `pr_watch.py --stop --repo <owner/name> --pr <n>` deletes the cursor;
- the epic run terminates.

`PR_WATCH_DEADLINE_S` and the deadline-to-park circuit breaker are retired.

### Where durability actually lives

Not in the cursor. On every wake the run loop already reconstructs state from
`gh`, so *"child Status = In Review with an open PR and no tick scheduled"* is
itself the re-arm signal. A dropped cursor, a killed process, a lost schedule:
the next wake re-arms the watch from GitHub. The watch is durable because it is
re-derivable, not because it is persisted.

## Who owns the tick

The largest structural change: **prose-gate resolution yields instead of
waiting.**

Today a drive subagent sits inside the parallel region waiting out CI and
CodeRabbit (`SKILL.md:452`). A subagent cannot survive a scheduled wake, so any
wait longer than its own lifetime is unrecoverable — precisely the abandoned
In-Progress child hazard SKILL.md already describes at `SKILL.md:503`.

New rule: when a drive subagent has nothing left to do but wait, it arms the
watch and **returns** with a `waiting` outcome. The run loop owns every tick and
re-dispatches a fresh drive subagent when activity fires.

Consequences:

- Nothing sits idle for an hour.
- The child is left at Status = In Review with an open PR — exactly the state
  the run loop re-derives from `gh` on any wake.
- Durability falls out of the existing stateless recovery instead of fighting
  it.
- `CHILD_DRIVE_CEILING_S` and its resume rule survive, but should now almost
  never fire.

Run-loop step 5 becomes concrete: **the wake delay is the minimum
`next_tick_in_s` across all live watches**, clamped to the harness range. One
scheduler for the whole epic, not one per child.

## Documentation changes

- `SKILL.md` §3, the `check-pending:<name>` bullet (`SKILL.md:355`): replace
  `--await <keys>` with the arm-and-yield protocol.
- `SKILL.md`, the drive subagent's prose-gate resolution step: same replacement,
  plus the yield rule.
- New subsection **"Waiting is a tick, never a sleep"**, replacing the
  `pr_watch.py --await` vocabulary section at
  `references/github-graphql.md:396` and its known-divergence note.
- `SKILL.md` tunables block: drop `PR_WATCH_DEADLINE_S=3600`; add
  `WATCH_FLOOR_S=15`, `WATCH_MULT=1.8`, `WATCH_CEIL_S=900`.
- `SKILL.md` circuit breakers: delete deadline-to-park. Prolonged silence is
  *reported*, never parked.
- `status.py`: surface live watches, e.g.
  `waiting 47m on PR #12, last activity: checks`.
- `README.md` script table: update the `pr_watch.py` row for the new flags and
  exit codes.

No `config.py` change. These tunables are prose plus flags and environment
variables; they are not `epic.yaml` keys.

## Testing

Outside-in TDD: every test below is written and watched fail before the
corresponding implementation.

Pure core, no I/O:

- `fingerprint()` — stability across identical input, and per-facet change
  detection for head, checks, reviews, threads, and comment count.
- `backoff()` — the sequence and its ceiling; jitter stays within ±20% under a
  seeded RNG.
- cursor round-trip; missing and corrupt files both yield step 0.

Two regression tests naming the original bugs directly:

1. A CodeRabbit `COMMENTED` review moves the fingerprint. (Today's `snapshot()`
   drops it, causing the hour-long false negative.)
2. An empty check rollup and zero unresolved threads report `waiting`, never
   settled. (Today's `settled_event()` returns instantly, causing the false
   positive.)

`main()` tests reuse the existing `monkeypatch pr_watch._fetch` pattern.

The current pr_watch suite at `tests/test_epic_scripts.py:767` covers
`settled_event`, `diff_event`, and the staircase `backoff`. All three
disappear, so that block is replaced wholesale rather than extended.

## Out of scope

- Durability across session end, machine reboot, or via cron/launchd.
- Any change to `mergeability.py`'s requirement derivation.
- Reworking the merge-queue or wave-selection logic in `schedule.py`.
