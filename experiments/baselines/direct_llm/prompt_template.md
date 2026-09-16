# Direct-LLM-Generation Prompt Template

Used verbatim (only `{report_text}` and `{schema_text}` substituted) by
`generate.py`. This is deliberately the *weakest* reasonable prompt: the
model gets the report and the schema, exactly as a rushed analyst might
paste them into a chat window, and nothing else — no typed IR to fill in,
no instruction to check field availability first, no evidence-citation
requirement, and critically **no automated check of its output afterward**.
That last point is what distinguishes this baseline from
"schema-constrained generation": both give the model the schema, but only
this one takes the output on faith.

```
You are helping a security analyst turn a threat report into a Spark
detection rule.

Below is a threat report and the schema of the authentication log you have
available to query. Write a Scala object with a `detect` method that takes
a Spark DataFrame of these events and returns a DataFrame of matching
alerts, implementing the behaviour described in the report.

Return only the Scala code, no explanation.

--- LOG SCHEMA ---
{schema_text}

--- THREAT REPORT ---
{report_text}
```
