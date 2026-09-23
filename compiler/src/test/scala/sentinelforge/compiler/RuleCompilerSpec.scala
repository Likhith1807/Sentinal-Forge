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
}
