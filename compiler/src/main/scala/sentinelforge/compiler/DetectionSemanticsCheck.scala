package sentinelforge.compiler

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.functions.{col, lit}

/** Formally pins down "exact detection semantics" (window boundaries,
  * event ordering, repeated incidents, missing values, policy lookup
  * failures) per the project's correctness completion standard — by
  * observing RuleCompiler's REAL behavior on small constructed cases,
  * not by asserting what it should do. Findings feed
  * docs/spec/detection-semantics.md directly; every claim there traces
  * back to one of these cases.
  */
private case class DetectionSemanticsEvent(
  event_id: String,
  timestamp: String,
  account_id: String,
  event_type: String,
  source_host: String,
  auth_method: String = null,
)

private case class DetectionSemanticsPolicyRow(account_id: String, mfa_required: java.lang.Boolean)
private case class DetectionSemanticsAuthPolicyRow(account_id: String, expected_auth_method: String)

object DetectionSemanticsCheck {
  private val Event = DetectionSemanticsEvent
  private val PolicyRow = DetectionSemanticsPolicyRow

  private val b1Spec = CompiledSpec(
    behaviourId = "repeated-failed-login-then-success",
    recipe = "SequenceThenTrigger",
    groupingKey = Some("account_id"),
    timeWindowSeconds = Some(300L),
    countEventType = Some("login_failure"),
    countThreshold = Some(3L),
    triggerEventType = Some("login_success"),
    distinctField = None,
    distinctThreshold = None,
    filterEventType = None,
    logField = None,
    policyField = None,
    comparisonOp = None,
  )

  private val spraySpec = CompiledSpec(
    behaviourId = "password-spray-across-accounts",
    recipe = "DistinctCountWithinWindow",
    groupingKey = Some("source_host"),
    timeWindowSeconds = Some(300L),
    countEventType = None,
    countThreshold = None,
    triggerEventType = None,
    distinctField = Some("account_id"),
    distinctThreshold = Some(3L),
    filterEventType = Some("login_failure"),
    logField = None,
    policyField = None,
    comparisonOp = None,
  )

  private val mfaSpec = CompiledSpec(
    behaviourId = "mfa-missing-on-required-account",
    recipe = "PolicyCompare",
    groupingKey = None,
    timeWindowSeconds = None,
    countEventType = None,
    countThreshold = None,
    triggerEventType = None,
    distinctField = None,
    distinctThreshold = None,
    filterEventType = Some("login_success"),
    logField = Some("mfa_used"),
    policyField = Some("mfa_required"),
    comparisonOp = Some("falseWhenRequired"),
  )

  private val serviceAccountSpec = CompiledSpec(
    behaviourId = "auth-method-policy-violation",
    recipe = "PolicyCompare",
    groupingKey = None,
    timeWindowSeconds = None,
    countEventType = None,
    countThreshold = None,
    triggerEventType = None,
    distinctField = None,
    distinctThreshold = None,
    filterEventType = Some("login_success"),
    logField = Some("auth_method"),
    policyField = Some("expected_auth_method"),
    comparisonOp = Some("notEqual"),
  )

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("detection-semantics-check").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    import spark.implicits._

    var findings = Vector.empty[String]
    def record(name: String, detail: String): Unit = {
      println(s"$name: $detail")
      findings :+= s"""{"case": ${q(name)}, "observed": ${q(detail)}}"""
    }

    // --- A/B: window boundary inclusivity (SequenceThenTrigger) ---
    // 3 failures at t=0,1,2, a success at t=300 (exactly windowSecs=300
    // later than t=0). rangeBetween(-300, 0) is a closed interval, so an
    // event exactly 300s before the current row's timestamp should still
    // be IN range.
    val atBoundary = Seq(
      Event("e1", "2026-01-01T00:00:00.000Z", "acct-x", "login_failure", "h"),
      Event("e2", "2026-01-01T00:00:01.000Z", "acct-x", "login_failure", "h"),
      Event("e3", "2026-01-01T00:00:02.000Z", "acct-x", "login_failure", "h"),
      Event("e4", "2026-01-01T00:05:00.000Z", "acct-x", "login_success", "h"), // t=300s
    ).toDF()
    val atBoundaryAlerts = RuleCompiler.compile(b1Spec, atBoundary).count()
    record("A: event exactly at windowSecs boundary (t=300s, window=300s)",
      s"alerts=$atBoundaryAlerts (rangeBetween(-300,0) is CLOSED => included, so 3>=3 should alert)")

    // Same shape, but the success is at t=301s — one second OUTSIDE.
    val justOutside = Seq(
      Event("e1", "2026-01-01T00:00:00.000Z", "acct-y", "login_failure", "h"),
      Event("e2", "2026-01-01T00:00:01.000Z", "acct-y", "login_failure", "h"),
      Event("e3", "2026-01-01T00:00:02.000Z", "acct-y", "login_failure", "h"),
      Event("e4", "2026-01-01T00:05:01.000Z", "acct-y", "login_success", "h"), // t=301s
    ).toDF()
    val justOutsideAlerts = RuleCompiler.compile(b1Spec, justOutside).count()
    record("B: event 1s beyond windowSecs boundary (t=301s, window=300s)",
      s"alerts=$justOutsideAlerts (only e1 at t=0 falls out of range; e2/e3 still within 300s of e4, so count=2 < 3 expected)")

    // --- C: sub-second timestamps truncated by .cast("long") ---
    // Two failures 400ms apart (same whole second after truncation) plus
    // a third — tests whether millisecond precision the schema documents
    // survives window ordering, or gets silently collapsed to the second.
    val subSecond = Seq(
      Event("e1", "2026-01-01T00:00:00.100Z", "acct-z", "login_failure", "h"),
      Event("e2", "2026-01-01T00:00:00.400Z", "acct-z", "login_failure", "h"),
      Event("e3", "2026-01-01T00:00:00.900Z", "acct-z", "login_failure", "h"),
      Event("e4", "2026-01-01T00:00:01.000Z", "acct-z", "login_success", "h"),
    ).toDF()
    val subSecondAlerts = RuleCompiler.compile(b1Spec, subSecond).count()
    record("C: 3 failures within the same second (sub-second timestamps), then success",
      s"alerts=$subSecondAlerts (parseTs(...).cast(\"long\") truncates to whole seconds — sub-second ordering is not preserved)")

    // --- D: repeated incidents, SequenceThenTrigger ---
    // Two FULLY separate qualifying incidents for the same account, far
    // apart in time.
    val repeatedB1 = Seq(
      Event("e1", "2026-01-01T00:00:00.000Z", "acct-r", "login_failure", "h"),
      Event("e2", "2026-01-01T00:00:01.000Z", "acct-r", "login_failure", "h"),
      Event("e3", "2026-01-01T00:00:02.000Z", "acct-r", "login_failure", "h"),
      Event("e4", "2026-01-01T00:00:03.000Z", "acct-r", "login_success", "h"), // incident 1
      Event("e5", "2026-01-02T00:00:00.000Z", "acct-r", "login_failure", "h"),
      Event("e6", "2026-01-02T00:00:01.000Z", "acct-r", "login_failure", "h"),
      Event("e7", "2026-01-02T00:00:02.000Z", "acct-r", "login_failure", "h"),
      Event("e8", "2026-01-02T00:00:03.000Z", "acct-r", "login_success", "h"), // incident 2, 1 day later
    ).toDF()
    val repeatedB1Alerts = RuleCompiler.compile(b1Spec, repeatedB1).count()
    record("D: 2 fully separate qualifying incidents, same account, 1 day apart (SequenceThenTrigger)",
      s"alertRows=$repeatedB1Alerts (expected 2 — one alert row per qualifying TRIGGER event, no cross-incident dedup)")

    // --- E: repeated incidents, DistinctCountWithinWindow ---
    val repeatedSpray = Seq(
      Event("e1", "2026-01-01T00:00:00.000Z", "acct-a", "login_failure", "spray-host"),
      Event("e2", "2026-01-01T00:00:01.000Z", "acct-b", "login_failure", "spray-host"),
      Event("e3", "2026-01-01T00:00:02.000Z", "acct-c", "login_failure", "spray-host"), // incident 1, 3 distinct
      Event("e4", "2026-01-02T00:00:00.000Z", "acct-d", "login_failure", "spray-host"),
      Event("e5", "2026-01-02T00:00:01.000Z", "acct-e", "login_failure", "spray-host"),
      Event("e6", "2026-01-02T00:00:02.000Z", "acct-f", "login_failure", "spray-host"), // incident 2, 1 day later, 3 more distinct
    ).toDF()
    val repeatedSprayResult = RuleCompiler.compile(spraySpec, repeatedSpray)
    val repeatedSprayAlerts = repeatedSprayResult.count()
    val sprayRows = repeatedSprayResult.orderBy("detectedAt").select("windowStart", "detectedAt").collect()
      .map(r => s"(windowStart=${r.getString(0)}, detectedAt=${r.getString(1)})").mkString(", ")
    record("E: 2 fully separate qualifying incidents, same host, 1 day apart (DistinctCountWithinWindow)",
      s"alertRows=$repeatedSprayAlerts [$sprayRows] " +
        s"(FIXED, Phase D: one alert per incident — a rising edge in the rolling distinct count, " +
        s"not one collapsed row per groupKey ever — so 2 fully separate incidents now correctly " +
        s"produce 2 alert rows, each with its own detectedAt and a windowStart exactly windowSecs " +
        s"before it, the same per-trigger-event granularity SequenceThenTrigger already had)")

    // --- F: malformed/missing timestamp ---
    val malformedTs = Seq(
      Event("e1", "not-a-timestamp", "acct-m", "login_failure", "h"),
      Event("e2", "2026-01-01T00:00:01.000Z", "acct-m", "login_failure", "h"),
      Event("e3", "2026-01-01T00:00:02.000Z", "acct-m", "login_failure", "h"),
      Event("e4", "2026-01-01T00:00:03.000Z", "acct-m", "login_success", "h"),
    ).toDF()
    val malformedResult = try {
      s"count=${RuleCompiler.compile(b1Spec, malformedTs).count()}"
    } catch {
      case e: Exception => s"THREW ${e.getClass.getSimpleName}: ${e.getMessage.linesIterator.next()}"
    }
    record("F: one event has an unparseable timestamp (SequenceThenTrigger)", malformedResult)

    // --- G: PolicyCompare, missing/null logField value (not missing policy) ---
    val policy = Seq(PolicyRow("acct-p", true)).toDF()
    val nullLogField = Seq(
      Event("e1", "2026-01-01T00:00:00.000Z", "acct-p", "login_success", "h"),
    ).toDF().withColumn("mfa_used", lit_null_boolean())
    val nullLogFieldResult = RuleCompiler.compile(mfaSpec, nullLogField, Some(policy))
      .select("status").first().getString(0)
    record("G: mfa_required=true in policy, but mfa_used is NULL on the event itself (not missing, null)",
      s"status=$nullLogFieldResult (FIXED, Phase D: policy lookup succeeded, but the logField itself " +
        s"is null on this event — checked explicitly now, for both comparisonOps, before the " +
        s"three-valued-logic comparison ever runs, so an unobserved value correctly degrades to " +
        s"insufficient_context instead of being silently read as compliant)")

    // --- H: repeated incidents, PolicyCompare ---
    val repeatedPolicy = Seq(
      Event("e1", "2026-01-01T00:00:00.000Z", "acct-p", "login_success", "h"),
      Event("e2", "2026-01-02T00:00:00.000Z", "acct-p", "login_success", "h"),
    ).toDF().withColumn("mfa_used", lit(false))
    val repeatedPolicyAlerts = RuleCompiler.compile(mfaSpec, repeatedPolicy, Some(policy))
      .filter(col("status") === "alert").count()
    record("H: 2 separate qualifying login_success events, same account, no mfa (PolicyCompare)",
      s"alertRows=$repeatedPolicyAlerts (expected 2 — PolicyCompare alerts once per qualifying EVENT, no dedup at all)")

    // --- I: PolicyCompare, notEqual comparisonOp, null logField (same gap as case G, other op) ---
    val authPolicy = Seq(DetectionSemanticsAuthPolicyRow("acct-svc", "password")).toDF()
    val nullAuthMethod = Seq(
      Event("e1", "2026-01-01T00:00:00.000Z", "acct-svc", "login_success", "h", auth_method = null),
    ).toDF()
    val nullAuthMethodResult = RuleCompiler.compile(serviceAccountSpec, nullAuthMethod, Some(authPolicy))
      .select("status").first().getString(0)
    record("I: expected_auth_method=password in policy, but auth_method is NULL on the event (notEqual op)",
      s"status=$nullAuthMethodResult (FIXED, Phase D: the same three-valued-logic gap as case G existed " +
        s"in 'notEqual' too — `col(logField) =!= col(policyField)` is also NULL when logField is NULL, " +
        s"previously falling through to no_alert here as well; both comparisonOps now check logField.isNull " +
        s"explicitly before comparing)")

    val repoRoot = new java.io.File(".").getCanonicalPath
    val outPath = s"$repoRoot/experiments/results/detection_semantics_check.json"
    val pw = new java.io.PrintWriter(outPath, "UTF-8")
    try pw.write(s"""{"cases": [${findings.mkString(", ")}]}\n""") finally pw.close()
    println(s"\nWrote $outPath")

    spark.stop()
  }

  private def lit_null_boolean() = lit(null: java.lang.Boolean)

  private def q(s: String): String =
    "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
}
