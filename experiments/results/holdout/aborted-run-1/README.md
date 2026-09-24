Aborted first evaluation run (kept for the record, not used for any reported number).

It was stopped because the frozen holdout contained a stale file (`HO-S-B2-03`, relabelled and renamed `HO-U-25`
during adjudication; the builder did not delete the old files). The holdout builder now removes anything not in
its manifest and the freeze was re-cut. `attempts.jsonl` here holds the LLM calls made before the abort.
