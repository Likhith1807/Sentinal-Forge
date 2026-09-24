# Holdout v2: what was recorded, and what was done afterwards

`summary.json` / `per_report.jsonl` / `FAILURE_ANALYSIS.md` are the **single run** on the frozen holdout (tag `holdout-v2-frozen`,
commit `ab8c006`, run before any change to the parser). Those are the v2 numbers. They are not edited.

Result of that run (evidence-only path): 19 of 30 supported reports compiled to the correct rule, 0 silently wrong,
5 of 41 must-not-compile reports wrongly accepted (`HO2-N-01`, `N-02`, `N-13`, `Q-02`, `Q-04`).

## Afterwards (post hoc - v2 is now consumed)

The five false accepts were three classes of parser gap, fixed in `sentinelforge/conditions.py` / `reconcile.py`:

| class | reports | fix |
|---|---|---|
| a value the author says is not settled ("possibly 15", "I'll confirm") | N-01 | `hedges` -> `VALUE_NOT_SETTLED` (needs_review) |
| an abandoned or negated intent ("no need to alert", "decided not to build it") | N-02, N-13 | extended `_NEGATED_INTENT` |
| a scope limit written outside the rule sentence ("Exclude hosts in the allow-list.", "limited to accounts in ...") | Q-02, Q-04 | document-wide exclusion scan, scope-limit qualifier |

Each is pinned by regression tests written in **new** wording (`tests/core/test_phrasing_classes.py`, "classes found by holdout v2"),
not the holdout sentences. Re-running the fixed parser over the v2 texts gives 0 false accepts and the same 19 supported compiled -
but that is **not a held-out measurement**, because the fixes were written after seeing those failures. The next honest measurement
needs a fresh holdout; none is claimed here.

The 11 supported reports that were refused are unchanged and are published in `FAILURE_ANALYSIS.md` (unusual key/value spellings such as
`min_distinct: 6` and `timeframe: 2m`, "a quarter of an hour", a checklist in a different word order). They were left in place on purpose.
