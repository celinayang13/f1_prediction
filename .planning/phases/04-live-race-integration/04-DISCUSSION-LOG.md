# Phase 4: Live Race Integration - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-07-09
**Phase:** 4-Live Race Integration
**Areas discussed:** Validation timing (real race vs replay), OpenF1 rate-limit/latency risk, Crash/hang detection & recovery, What counts as "validated"

---

## Validation timing — real race vs replay

| Option | Description | Selected |
|--------|-------------|----------|
| Yes, upcoming race weekend | Wait for and test against a real live session soon | |
| No — replay only for now | Use scripts/replay_race.py as a stand-in; treat a real race as a future follow-up | |
| Not sure / don't know the schedule | Let Claude check F1 calendar or default to replay | |

**User's choice (free text):** "one in 2 weeks, lets do replay for now" — a real race weekend exists ~2026-07-23, but the user chose to validate now via replay.
**Notes:** Follow-up question asked whether Phase 4 should be scoped as replay-only (complete once replay passes) or span both replay-now and a real-race check in 2 weeks.

| Option | Description | Selected |
|--------|-------------|----------|
| Phase 4 = replay only, live race is separate | Phase 4 completes once replay validation passes; real race check is a separate future task | ✓ |
| Phase 4 spans both (replay now, live check in 2 weeks) | Phase 4 stays open until the real race check also passes | |

**User's choice:** Phase 4 = replay only, live race is separate.

---

## OpenF1 rate-limit/latency risk

| Option | Description | Selected |
|--------|-------------|----------|
| Accept free tier, observe only | Use free tier as-is, document actual latency during testing | ✓ |
| Upgrade to sponsor tier for this test | Pay ~€9.90/mo before the live race test | |
| Not applicable during replay | Replay reads local SQLite, not OpenF1 — nothing to decide now | |

**User's choice:** Accept free tier, observe only.
**Notes:** Since Phase 4 is now replay-only (per prior decision), this risk is largely deferred in practice to the future real-race check, but the user's stated preference is to accept free tier rather than pre-emptively upgrade.

---

## Crash/hang detection & recovery

| Option | Description | Selected |
|--------|-------------|----------|
| Rely on existing logging only | poll_once()'s existing INFO logs are sufficient; watch manually | |
| Add a lightweight heartbeat/last-updated check | Add explicit staleness visibility | |
| Claude decides based on what's simplest | No strong preference | ✓ |

**User's choice:** Claude decides based on what's simplest.

| Option | Description | Selected |
|--------|-------------|----------|
| Manual restart is fine | Local dev tool — re-run the pipeline command manually on crash | ✓ |
| Auto-restart via a supervisor | Wrap the process for automatic restart on crash | |

**User's choice:** Manual restart is fine.

---

## What counts as "validated"

| Option | Description | Selected |
|--------|-------------|----------|
| Written VALIDATION.md checklist | Produce a doc mapping each success criterion to an observed result | ✓ |
| Verbal confirmation is enough | No new doc; just watch and confirm in conversation | |

**User's choice:** Written VALIDATION.md checklist.

| Option | Description | Selected |
|--------|-------------|----------|
| Implicitly covered | Running pipeline + dashboard concurrently during replay already exercises WAL/concurrency | ✓ |
| Add an explicit check | Add a specific check/log for SQLITE_BUSY/locked errors | |

**User's choice:** Implicitly covered.

---

## Claude's Discretion

- Exact mechanism for demonstrating "no crash or hang" during the ~60-lap replay run (D-03) — simplest approach preferred, likely leaning on `pipeline.py`'s existing per-cycle INFO logging rather than new monitoring infrastructure.

## Deferred Ideas

- Real live-race validation against actual OpenF1 data (~2026-07-23) — deferred to a separate future task/phase, not part of Phase 4.
- OpenF1 sponsor tier upgrade decision — deferred until the real live-race check.
- Auto-restart/process-supervisor for the pipeline process — rejected for v1.
- Explicit WAL/concurrency stress-test script — rejected; concurrent replay run considered sufficient.
