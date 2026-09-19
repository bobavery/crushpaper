# Resume notes (paused 2026-09-19 ~16:15 UTC)

Temporary file; delete when the work is finished.

## Done and pushed
- `btc_lab/` package (engine, strategies, data, metrics, theory, paper loop, CLI), 29 tests passing.
- Hourly Bitstamp dataset 2017 → 2026-09-19 in `data/`, builder in `scripts/build_dataset.py`.
- Flip sweeps (8 settings × 300 configs), grid/trend baselines, theory notes in `results/`.
- `docs/RESULTS.md` (sections 1, 3, 4, 5, caveats updated after the code review; section 2 tables and
  the section 1 theory table still quote the PRE-review sweep/theory numbers).
- `prompts/03_daily_trading_desk.md` (draft, not yet reviewed).
- Code review workflow: 17 confirmed findings, all fixed except the documentation ones that wait on
  the research/critique outputs (R7: docs/RESEARCH.md, docs/CRITIQUE.md, prompts/01, prompts/02).

## In progress when paused
- DONE: `scripts/run_sweeps.sh` re-ran with the corrected fill rule; `results/` is current and
  committed. Headline counts are unchanged (0/300 beat hold under real fees, 0/240 stop-protected
  configs positive). STILL TO DO: update `docs/RESULTS.md` section 2 tables and the section 1 theory
  table from `results/sweep_summary.txt` and `results/theory_notes.txt` (theory now prints separate
  target/stop round-trip costs, 20000 paths).
- Critique/research workflow (run id wf_f11a278a-701) was stopped after: 6 critics, 7 research
  sweeps, merge-findings and completeness-critic finished. Their structured outputs are saved in
  `docs/_workfiles/critique_research_partial.json`. Remaining stages: refute findings (batched),
  follow-up sweeps (3), fact-check key claims (batched), then synthesis (forecast prompt v2 from two
  drafts + editor, weekly holder prompt, research brief). Either resume the workflow
  (`Workflow({scriptPath: <session script>, resumeFromRunId: 'wf_f11a278a-701'})`, cached prefix) if the
  container survived, or write a synthesis-only workflow that takes the saved JSON as `args`.

## Still to write
- `prompts/01_forecast_v2.md`, `prompts/02_weekly_holder_checkin.md` (from the critique findings).
- `docs/CRITIQUE.md` (surviving/refuted findings per lens, what changed in v2).
- `docs/RESEARCH.md` (retail evidence, fees/taxes 2026, strategy evidence, target math, LLM agents,
  paper-trading platforms table, free data sources table, refuted claims).
- README pointers already reference these files; remove the pointers if they are not produced.
- Review `prompts/03_daily_trading_desk.md` against the research (data-source availability).
- Final: delete `docs/_workfiles/`, run tests, commit, push, write the summary for the user
  (include: exchange APIs are blocked from this cloud container, so the paper trader must run on the
  user's own computer or an environment with open network; offer to schedule the weekly prompt).
