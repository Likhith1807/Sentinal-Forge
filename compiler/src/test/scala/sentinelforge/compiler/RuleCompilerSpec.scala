package sentinelforge.compiler

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.functions.lit
import org.scalatest.BeforeAndAfterAll
import org.scalatest.funsuite.AnyFunSuite
import org.scalatest.matchers.should.Matchers

// Top-level (not nested inside the spec class), matching DetectionSemanticsCheck.scala's
// convention — Spark's reflection-based Encoders.product needs a stable, non-inner-class path
// to the type; a case class nested inside a class body produces a qualified name like
// `RuleCompilerSpec$Ev` that its generated code cannot correctly resolve a field accessor for
// ("No applicable constructor/method found for zero actual parameters"), failing every test.
private case class RuleCompilerSpecEvent(event_id: String, timestamp: String, account_id: String,
                                         event_type: String, source_host: String, auth_method: String = null)
private case class RuleCompilerSpecPolicyRow(account_id: String, mfa_required: java.lang.Boolean = null,
                                             expected_auth_method: String = null)

/** Real `sbt test` coverage for `RuleCompiler`'s core semantics — fast, deterministic,
  * in-memory-only cases. This is deliberately a small, targeted set, not a duplicate of
  * `DetectionSemanticsCheck.scala`/`DistinctCountBoundaryCheck.scala` (the project's own
  * "observe real behaviour, write it up" checks, run via `sbt runMain` and feeding
  * `docs/spec/detection-semantics.md` directly): those stay the source of truth for documented
  * semantics; this suite exists so `sbt test` — previously an unused dependency with zero
  * suites — has real, fast, CI-friendly regression coverage that fails loudly on `sbt test`
  * without needing a `runMain` invocation or a results JSON to inspect.
  */
class RuleCompilerSpec extends AnyFunSuite with BeforeAndAfterAll with Matchers {

  private var spark: SparkSession = _

  override def beforeAll(): Unit = {
    spark = SparkSession.builder().appName("rule-compiler-spec").master("local[1]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
  }

  override def afterAll(): Unit = spark.stop()

  private implicit val evEncoder: org.apache.spark.sql.Encoder[RuleCompilerSpecEvent] =
    org.apache.spark.sql.Encoders.product[RuleCompilerSpecEvent]
  private implicit val policyEncoder: org.apache.spark.sql.Encoder[RuleCompilerSpecPolicyRow] =
    org.apache.spark.sql.Encoders.product[RuleCompilerSpecPolicyRow]

  private val b1Spec = CompiledSpec(
    behaviourId = "repeated-failed-login-then-success", recipe = "SequenceThenTrigger",
    groupingKey = Some("account_id"), timeWindowSeconds = Some(120L),
    countEventType = Some("login_failure"), countThreshold = Some(3L),
    triggerEventType = Some("login_success"), distinctField = None, distinctThreshold = None,
    filterEventType = None, logField = None, policyField = None, comparisonOp = None,
  )

  private val b2Spec = CompiledSpec(
    behaviourId = "password-spray-across-accounts", recipe = "DistinctCountWithinWindow",
    groupingKey = Some("source_host"), timeWindowSeconds = Some(600L),
    countEventType = None, countThreshold = None, triggerEventType = None,
    distinctField = Some("account_id"), distinctThreshold = Some(3L),
    filterEventType = Some("login_failure"), logField = None, policyField = None, comparisonOp = None,
  )

  private val mfaSpec = CompiledSpec(
    behaviourId = "mfa-missing-on-required-account", recipe = "PolicyCompare",
    groupingKey = None, timeWindowSeconds = None, countEventType = None, countThreshold = None,
    triggerEventType = None, distinctField = None, distinctThreshold = None,
    filterEventType = Some("login_success"), logField = Some("mfa_used"),
    policyField = Some("mfa_required"), comparisonOp = Some("falseWhenRequired"),
  )

  test("SequenceThenTrigger alerts once the failure count meets the threshold, not before") {
    val events = spark.createDataset(Seq(
      RuleCompilerSpecEvent("e1", "2026-01-01T00:00:00.000Z", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("e2", "2026-01-01T00:00:01.000Z", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("e3", "2026-01-01T00:00:02.000Z", "acct", "login_success", "h"),
    )).toDF()
    RuleCompiler.compile(b1Spec, events).count() shouldBe 0L // only 2 failures, threshold is 3
  }

  test("SequenceThenTrigger: the boundary event exactly windowSecs earlier is included (closed interval)") {
    val events = spark.createDataset(Seq(
      RuleCompilerSpecEvent("e1", "2026-01-01T00:00:00.000Z", "acct", "login_failure", "h"), // t=0, exactly 120s before e4
      RuleCompilerSpecEvent("e2", "2026-01-01T00:00:01.000Z", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("e3", "2026-01-01T00:00:02.000Z", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("e4", "2026-01-01T00:02:00.000Z", "acct", "login_success", "h"), // t=120
    )).toDF()
    val alerts = RuleCompiler.compile(b1Spec, events).collect()
    alerts should have length 1
    alerts.head.getAs[String]("triggeringEventId") shouldBe "e4"
  }

  test("DistinctCountWithinWindow: regression for the Phase D fix — one alert per incident, not one per group ever") {
    val events = spark.createDataset(Seq(
      // Incident 1: 3 distinct accounts fail from host "h" within the window.
      RuleCompilerSpecEvent("e1", "2026-01-01T00:00:00.000Z", "a1", "login_failure", "h"),
      RuleCompilerSpecEvent("e2", "2026-01-01T00:00:01.000Z", "a2", "login_failure", "h"),
      RuleCompilerSpecEvent("e3", "2026-01-01T00:00:02.000Z", "a3", "login_failure", "h"),
      // Incident 2: a second, fully separate breach on the SAME host, a day later.
      RuleCompilerSpecEvent("e4", "2026-01-02T00:00:00.000Z", "a4", "login_failure", "h"),
      RuleCompilerSpecEvent("e5", "2026-01-02T00:00:01.000Z", "a5", "login_failure", "h"),
      RuleCompilerSpecEvent("e6", "2026-01-02T00:00:02.000Z", "a6", "login_failure", "h"),
    )).toDF()
    val alerts = RuleCompiler.compile(b2Spec, events).collect()
    alerts should have length 2 // not 1 — this is exactly the bug independent review found and Phase D fixed
    alerts.map(_.getAs[String]("triggeringEventId")).toSet shouldBe Set("e3", "e6")
  }

  test("DistinctCountWithinWindow: counting events is not the same as counting distinct values") {
    val events = spark.createDataset(Seq(
      RuleCompilerSpecEvent("e1", "2026-01-01T00:00:00.000Z", "a1", "login_failure", "h"),
      RuleCompilerSpecEvent("e2", "2026-01-01T00:00:01.000Z", "a1", "login_failure", "h"), // same account again
      RuleCompilerSpecEvent("e3", "2026-01-01T00:00:02.000Z", "a2", "login_failure", "h"), // only 2 distinct accounts total
    )).toDF()
    RuleCompiler.compile(b2Spec, events).count() shouldBe 0L // threshold is 3 distinct accounts
  }

  test("PolicyCompare: regression for the Phase D fix — a null log field is insufficient_context, not compliant") {
    val events = spark.createDataset(Seq(RuleCompilerSpecEvent("e1", "2026-01-01T00:00:00.000Z", "acct", "login_success", "h")))
      .toDF().withColumn("mfa_used", lit(null: java.lang.Boolean))
    val policy = spark.createDataset(Seq(RuleCompilerSpecPolicyRow("acct", mfa_required = true))).toDF()
    val status = RuleCompiler.compile(mfaSpec, events, Some(policy)).select("status").collect().map(_.getString(0))
    status should contain theSameElementsAs Seq("insufficient_context") // was "no_alert" before the fix
  }

  test("PolicyCompare: a missing policy row degrades to insufficient_context") {
    val events = spark.createDataset(Seq(RuleCompilerSpecEvent("e1", "2026-01-01T00:00:00.000Z", "ghost", "login_success", "h")))
      .toDF().withColumn("mfa_used", lit(false))
    val policy = spark.createDataset(Seq(RuleCompilerSpecPolicyRow("someone-else", mfa_required = true))).toDF()
    val status = RuleCompiler.compile(mfaSpec, events, Some(policy)).select("status").collect().map(_.getString(0))
    status should contain theSameElementsAs Seq("insufficient_context")
  }

  test("an unrecognised recipe fails loudly rather than silently doing nothing") {
    val badSpec = b1Spec.copy(recipe = "NotARealRecipe")
    val events = spark.createDataset(Seq(RuleCompilerSpecEvent("e1", "2026-01-01T00:00:00.000Z", "acct", "login_failure", "h"))).toDF()
    an[IllegalArgumentException] should be thrownBy RuleCompiler.compile(badSpec, events)
  }

  // ---------------------------------------------------------------------------------------------------
  // AUDIT REGRESSIONS (2026-09): event-processing defects. Each test names the defect it pins.
  // ---------------------------------------------------------------------------------------------------

  private val b1Spec2 = b1Spec.copy(countThreshold = Some(2L), timeWindowSeconds = Some(10L))

  test("timestamp precision: an event 1 ms outside the window is OUTSIDE (whole-second truncation used to include it)") {
    // failure at 00:00:00.000, success at 00:00:10.001 -> the failure is 10.001 s earlier, window is 10 s.
    val events = spark.createDataset(Seq(
      RuleCompilerSpecEvent("f1", "2026-01-01T00:00:00.500Z", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("f2", "2026-01-01T00:00:05.000Z", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("s1", "2026-01-01T00:00:10.501Z", "acct", "login_success", "h"), // f1 is 10.001 s back
    )).toDF()
    RuleCompiler.compile(b1Spec2, events).count() shouldBe 0L
    val inside = spark.createDataset(Seq(
      RuleCompilerSpecEvent("f1", "2026-01-01T00:00:00.500Z", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("f2", "2026-01-01T00:00:05.000Z", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("s1", "2026-01-01T00:00:10.500Z", "acct", "login_success", "h"), // exactly 10.000 s back: closed
    )).toDF()
    RuleCompiler.compile(b1Spec2, inside).count() shouldBe 1L
  }

  test("duplicate delivery: a redelivered event_id counts once and cannot multiply alerts") {
    val events = spark.createDataset(Seq(
      RuleCompilerSpecEvent("f1", "2026-01-01T00:00:00.000Z", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("f1", "2026-01-01T00:00:00.000Z", "acct", "login_failure", "h"), // exact redelivery
      RuleCompilerSpecEvent("s1", "2026-01-01T00:00:02.000Z", "acct", "login_success", "h"),
      RuleCompilerSpecEvent("s1", "2026-01-01T00:00:02.000Z", "acct", "login_success", "h"),
    )).toDF()
    // threshold 2: one real failure (delivered twice) must NOT satisfy it
    RuleCompiler.compile(b1Spec2, events).count() shouldBe 0L
  }

  test("malformed timestamps are quarantined with a reason, never silently dropped or sorted as null") {
    val events = spark.createDataset(Seq(
      RuleCompilerSpecEvent("f1", "2026-01-01T00:00:00.000Z", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("f2", "not-a-timestamp", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("f3", "2026-01-01T00:00:03", "acct", "login_failure", "h"),        // no zone: ambiguous
      RuleCompilerSpecEvent("f4", "2026-13-45T00:00:00.000Z", "acct", "login_failure", "h"),   // matches shape, not a date
      RuleCompilerSpecEvent("f5", null, "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("s1", "2026-01-01T00:00:02.000Z", "acct", "login_success", "h"),
    )).toDF()
    val (clean, quarantine) = RuleCompiler.prepare(b1Spec2, events)
    clean.count() shouldBe 2L
    quarantine.select("event_id", "reason").collect().map(r => r.getString(0) -> r.getString(1)).toMap shouldBe
      Map("f2" -> "malformed_timestamp", "f3" -> "malformed_timestamp", "f4" -> "malformed_timestamp", "f5" -> "null_timestamp")
    // only ONE valid failure survives, so threshold 2 is not met — the bad rows did not become failures either
    RuleCompiler.compile(b1Spec2, events).count() shouldBe 0L
  }

  test("timestamps with a numeric UTC offset are normalised, not treated as UTC") {
    val events = spark.createDataset(Seq(
      RuleCompilerSpecEvent("f1", "2026-01-01T02:00:00.000+02:00", "acct", "login_failure", "h"), // = 00:00:00Z
      RuleCompilerSpecEvent("f2", "2026-01-01T00:00:05.000Z", "acct", "login_failure", "h"),
      RuleCompilerSpecEvent("s1", "2026-01-01T00:00:08.000Z", "acct", "login_success", "h"),
    )).toDF()
    RuleCompiler.compile(b1Spec2, events).count() shouldBe 1L
  }

  test("duplicate policy rows cannot multiply alerts: identical rows collapse, conflicting rows -> insufficient_context") {
    val events = spark.createDataset(Seq(
      RuleCompilerSpecEvent("e1", "2026-01-01T00:00:00.000Z", "same", "login_success", "h"),
      RuleCompilerSpecEvent("e2", "2026-01-01T00:00:01.000Z", "clash", "login_success", "h"),
    )).toDF().withColumn("mfa_used", lit(false))
    val policy = spark.createDataset(Seq(
      RuleCompilerSpecPolicyRow("same", mfa_required = true), RuleCompilerSpecPolicyRow("same", mfa_required = true),
      RuleCompilerSpecPolicyRow("clash", mfa_required = true), RuleCompilerSpecPolicyRow("clash", mfa_required = false),
    )).toDF()
    val out = RuleCompiler.compile(mfaSpec, events, Some(policy)).select("groupKey", "status", "reason").collect()
      .map(r => r.getString(0) -> (r.getString(1), r.getString(2))).toMap
    RuleCompiler.compile(mfaSpec, events, Some(policy)).count() shouldBe 2L                  // one result per event, not per policy row
    out("same") shouldBe (("alert", "policy_violated"))
    out("clash") shouldBe (("insufficient_context", "policy_conflict"))
  }

  test("data that cannot evaluate the rule fails with a structured schema error, not a Spark stack trace") {
    val noHost = spark.createDataset(Seq(RuleCompilerSpecEvent("e1", "2026-01-01T00:00:00.000Z", "a", "login_failure", "h"))).toDF().drop("source_host")
    val ex = the[RuleCompiler.SchemaMismatchException] thrownBy RuleCompiler.compile(b2Spec, noHost)
    ex.problems.map(_.column) should contain ("source_host")
    val wrongType = spark.createDataset(Seq(RuleCompilerSpecEvent("e1", "2026-01-01T00:00:00.000Z", "a", "login_success", "h")))
      .toDF().withColumn("mfa_used", lit("false"))
    val pol = spark.createDataset(Seq(RuleCompilerSpecPolicyRow("a", mfa_required = true))).toDF()
    val ex2 = the[RuleCompiler.SchemaMismatchException] thrownBy RuleCompiler.compile(mfaSpec, wrongType, Some(pol))
    ex2.problems.map(_.column) should contain ("mfa_used")
  }

  test("evidence: an alert lists exactly the events inside its window, oldest first") {
    val events = spark.createDataset(Seq(
      RuleCompilerSpecEvent("old", "2026-01-01T00:00:00.000Z", "a1", "login_failure", "h"),   // outside (600 s window)
      RuleCompilerSpecEvent("e1", "2026-01-01T01:00:00.000Z", "a1", "login_failure", "h"),
      RuleCompilerSpecEvent("e2", "2026-01-01T01:00:01.000Z", "a2", "login_failure", "h"),
      RuleCompilerSpecEvent("e3", "2026-01-01T01:00:02.000Z", "a3", "login_failure", "h"),
    )).toDF()
    val alert = RuleCompiler.compileWithEvidence(b2Spec, events).collect().head
    alert.getAs[String]("triggeringEventId") shouldBe "e3"
    alert.getAs[String]("windowStart") shouldBe "2026-01-01T00:50:02.000Z"   // detectedAt - 600 s, rendered in UTC
    alert.getAs[String]("detectedAt") shouldBe "2026-01-01T01:00:02.000Z"
    val ids = alert.getSeq[org.apache.spark.sql.Row](alert.fieldIndex("evidence")).map(_.getAs[String]("eventId"))
    ids shouldBe Seq("e1", "e2", "e3")
  }
}
