# Demo script (about 3 minutes)

Three moments, in this order, because together they show the whole idea: **one detection that is traceable, one refusal that is
justified, one failure the system predicts before it happens.** Nothing is staged — the demo dataset and the ten reports are
committed (`data/demo/`), and `python scripts/demo.py` prints the same three moments with real output if you want a terminal version.

Start the app: `python -m uvicorn dashboard.backend.main:app --port 8000`, open <http://127.0.0.1:8000>.

## 0:00 — Say what it is (10 s)

> "SENTINEL Forge turns a threat-report paragraph into a Spark detection — but only when the *text itself* justifies every number.
> A language model is allowed to suggest; only a quote from the report is allowed to decide."

## 0:10 — Moment 1: a detection (60 s) — *Report workspace*

1. Choose the sample **"Brute force against the VPN"** (or paste `data/demo/reports/01-brute-force-vpn.md`). Run the analysis.
   Point at the job states moving: *queued → extracting → validating → ready*.
2. Point at the two extracted values, **5 or more** failed logins and a **2-minute** window, each highlighted in the report with its quote.
   Say: *"'14' also appears in the text. It is a narrative number, not a rule, and it was not used."*
3. *Validation review*: the dataset supports every field. **Compile** → rule version 1, with a hash.
4. **Run** on the demo dataset (fresh job), then open an alert in *Investigation* and its **evidence record**: why (the quote), what it
   means, what data it needs, which events fired, how it was executed, and what is *not* known. Say: *"This is an evidence record,
   not a proof. The quote is checked to exist; whether it means what the rule says is a person's call."*
5. Back in *Validation review*, **Approve.** Point at the confirmation: it names the rule version and the run.

## 1:10 — Moment 2: a justified refusal (50 s)

1. Paste the sample **"Impossible travel"**. The analysis ends *rejected*. Point at the reason and the sentence that caused it:
   the schema has **no location field**, so "impossible travel" cannot be detected from this log — and the system does not pretend.
2. Paste **"Contradictory windows"**: refused (reason `WINDOW_CONFLICT`), with both windows quoted. Say: *"It will not choose for you."*
3. Paste **"Ninety seconds"**: compiles to **90 seconds**, quote shown. Say: *"An earlier version of this pipeline turned that into
   90 minutes. That was one of the audit's silent failures; it now has a regression test."*

## 2:00 — Moment 3: the data changes under an approved rule (60 s) — *Validation review → Schema-change impact*

1. With the brute-force rule (needs `account_id`) and a password-spray rule (needs `source_host`) both approved, apply the schema
   change **remove `source_host`**.
2. The impact panel: the spray rule is **paused** with the blocked condition named; the brute-force rule is **unaffected**.
3. Try to resume the spray rule: refused — the dataset cannot evaluate it. Restore the previous schema version, then resume: a revalidation run executes on the
   current data, and only when it succeeds does the rule return to *approved*, with a recorded decision.
   Say: *"You find out before production, not after."*

## 3:00 — Close (10 s)

Open **Evaluation**. Say: *"Every number here is labelled — regression, frozen holdout, or synthetic — with the command that
reproduces it, including where it failed."* Stop on the holdout table, which shows the raw model's silent errors next to the
evidence-checked path's zero.

## What to leave in, not smooth over

* If a report goes to *needs review*, keep it in the recording. That is the design working.
* Do not describe the benchmarks as distributed or the data as real. Both are stated on the Evaluation screen and in `docs/limitations.md`.
* Do not say a human reviewed the holdout. One annotator and one blind LLM reviewer did; a human second reviewer is still open.
