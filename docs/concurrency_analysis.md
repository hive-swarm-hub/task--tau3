# Concurrency Analysis — `run_generic_full.log`

## Observed rate-limit pattern

Log range: 02:05:34 → 02:25:17 (wall ~19m43s), 97/97 tasks started, `max_concurrency=12`.

- **Unique tasks hitting any `RateLimitError`**: **40 / 97** (41%)
- **Unique tasks permanently failed** (>= 4 attempts, excluded via the
  "Excluding 27 infrastructure error simulation(s)" warning): **27**
  - 26 of the 27 are TPM `RateLimitError`; 1 (task_022) is an unrelated
    `InternalServerError` connection blip at 02:06 before the TPM wall was hit.
- **Peak TPM usage** observed in error payloads: **Used 2,000,000** — the full
  OpenAI org ceiling — seen in 75 of 89 reported `Used` values. Lowest
  observed `Used` in an error was 1,973,837 (i.e. every single error was
  within 1.3% of the ceiling; the ceiling was the binding constraint).
- **Timing distribution of permanent failures** by launch position:
  - Early (pos 1–32): **3** tasks (task_022, task_026, task_029)
  - Mid  (pos 33–64): **14** tasks
  - Late (pos 65–97): **10** tasks
  - First RL error: 02:08:25 (task_043, launch pos 37) — ~3 min in, after
    the initial batch of 12 had spent enough tokens to saturate the 60s window.
- **Rate-limit events per minute**: 0 in 02:05–02:07, then 2–9/min sustained
  from 02:08 through 02:23 — i.e. TPM was pegged for ~15 of the run's 20 min.

## Recommendation: drop to `max_concurrency=8`

At 12 parallel sims the run spent three-quarters of wall time at the TPM
ceiling and silently dropped 27/97 tasks from the denominator — the reported
pass^1 is not reproducible. Scaling linearly, 8 parallel sims target a peak
of ~1.33M TPM (67% of the 2M ceiling), which leaves enough headroom that
transient bursts are absorbed by the litellm retry (4 attempts) instead of
exhausting it. Concurrency=6 is unnecessarily conservative given the
headroom at 8; concurrency=4 wastes ~60% of dev wall time; concurrency=12
is the status quo that just produced the polluted run.

## Code change

`eval/run_eval.py` line ~32: `MAX_CONCURRENCY` default dropped from `12` to
`8`. The previous value is kept as a commented-out `MAX_CONCURRENCY = ... "12"`
line directly above, with an inline note explaining that it saturates TPM and
should only be restored if OpenAI raises the org's TPM limit. No other files
touched. `EVAL_CONCURRENCY` env var still overrides.

## Expected wall-clock impact

- concurrency=12: ~19m43s observed (but 27 tasks polluted)
- concurrency=8:  expected ~24–26 min for the full 97-task eval (linear scale
  from observed throughput, plus a small win from not wasting cycles on 4×
  retries of 40 tasks). Cost is unchanged — same tokens, just spread over
  more minutes. Inner-loop dev should keep using `EVAL_LITE=1` (~3 min).
