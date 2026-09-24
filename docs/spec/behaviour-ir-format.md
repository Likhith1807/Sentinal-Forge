> **Historical document.** Written during an earlier phase of the project and kept as a record. Numbers here were measured on the original 44-report set and the pre-audit pipeline; the current, audited results and limits are in [`docs/evaluation.md`](../evaluation.md) and [`docs/limitations.md`](../limitations.md).

# Typed Behaviour IR (v1)

This is the intermediate representation Stage 2 (Understand the Report)
produces and Stage 3 (Validate the Specification) checks. It sits between
free-text extraction and compiled Spark code, and it is the single artifact
every later component must agree on: the NLP pipeline emits it, the compiler
consumes it, and the dashboard displays it back to the analyst alongside its
evidence.

A worked instance for the canonical example is at
[`login-brute-force-001.json`](../../data/samples/ir/login-brute-force-001.json).

## Shape

```
BehaviourSpec
├── behaviourId        string                     stable id, e.g. "repeated-failed-login-then-success"
├── description         string                     one-line human summary
├── attackTechniques     string[]                   ATT&CK technique IDs, e.g. ["T1110", "T1078"]
├── entities             Entity[]                   things the behaviour refers to
│   ├── name             string
│   ├── type              string                     e.g. "account", "host"
│   └── provenance        Provenance
├── requiredFields       FieldRef[]                 schema fields this behaviour reads
│   ├── field             string                     must exist in the target log schema
│   └── provenance        Provenance
├── predicates           Predicate[]
│   ├── field             string
│   ├── op                 string                     eq | neq | gte | lte | in
│   ├── value              any
│   └── provenance        Provenance
├── sequence             Step[]                     ordered event pattern
│   ├── step               string                     label, e.g. "failures", "success"
│   ├── matches            Predicate[]                predicates this step's events must satisfy
│   ├── count               Threshold | null           e.g. { op: "gte", value: 5 }
│   └── provenance         Provenance
├── groupingKey           string                     field events are correlated on, e.g. "account_id"
├── timeWindow            Duration                   e.g. { amount: 2, unit: "minutes" }
├── windowType            "sliding" | "tumbling"
└── validation            ValidationResult           filled in by Stage 3, not Stage 2
    ├── status             "supported" | "rejected" | "pending"
    ├── missingFields      string[]
    └── notes              string[]

Provenance = { kind: "source-span", reportId, text, charStart, charEnd }
           | { kind: "analyst-assumption", note, confirmedBy, confirmedAt }
```

## Why `provenance` is mandatory, not optional

Every entity, required field, predicate, and sequence step carries a
`provenance` value. This is the mechanism behind SENTINEL Forge's evidence
traceability claim — a condition with no provenance is, by construction, not
representable in this IR. A field's provenance is either a literal span into
the source report, or an explicit `analyst-assumption` record showing who
confirmed it and when (used for thresholds the narrative left implicit, like
"5 failures" and "2 minutes" in the canonical example).

## Why `validation` is a separate, later-filled block

Stage 2 never sets `validation` — it only knows what the report says, not
what the log schema can observe. Stage 3 is the only stage allowed to write
`validation.status`. Keeping it a distinct block (rather than, say, a
boolean on each field) means Stage 4's compiler has one field to check
before it will touch a spec at all: `validation.status == "supported"`.
