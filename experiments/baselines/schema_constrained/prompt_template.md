# Schema-Constrained Generation Prompt Template

Used only when Stage 3 has already marked the extracted spec `supported`.
Unlike `direct_llm/prompt_template.md`, which hands over the whole schema
and lets the model choose whatever fields it wants, this one hands over
**only the fields Stage 3 already confirmed are safe** and explicitly
forbids anything else — the model cannot reach for `source_ip` even if the
report mentions it, because it's never told the field exists.

```
You are helping a security analyst turn a threat report into a Spark
detection rule. This behaviour has ALREADY been validated against the
available log schema. You may use ONLY these confirmed-observable fields
— do not reference any other field, even if the report mentions one:

{validated_fields}

Write a Scala object with a `detect` method that takes a Spark DataFrame
of authentication events{policy_param} and returns a DataFrame of matching
alerts, implementing the behaviour described below, using ONLY the fields
listed above.

Return only the Scala code, no explanation.

--- THREAT REPORT ---
{report_text}
```
