package sentinelforge.compiler

import org.apache.spark.sql.SparkSession

/** Permanent regression check for a real correctness fix: `RuleCompiler`'s
  * `DistinctCountWithinWindow` recipe used to call `approx_count_distinct`
  * (Spark's HyperLogLog-based cardinality estimator) inside a sliding
  * window, to decide a security detection threshold. That's an
  * approximation used for an exact yes/no decision, with no justification
  * or evaluation ever recorded for why the approximation was safe — it was
  * replaced with an exact count (`size(collect_set(...))`) instead. This
  * check exercises exactly the boundary an approximation error would show
  * up at, and the duplicate-event case a naive `count(*)` (as opposed to a
  * genuinely distinct count) would get wrong in the other direction.
  *
  * Small in-memory event set, no dependency on data/processed/ — this is a
  * unit-scale check of one recipe's semantics, not a replay against real
  * data (ReplayCheck already covers that).
  */
private case class DistinctCountBoundaryEvent(
  event_id: String,
  timestamp: String,
  account_id: String,
  event_type: String,
  source_host: String,
)

object DistinctCountBoundaryCheck {
  private val Event = DistinctCountBoundaryEvent

  private val spraySpec = CompiledSpec(
    behaviourId = "password-spray-across-accounts",
    recipe = "DistinctCountWithinWindow",
    groupingKey = Some("source_host"),
    timeWindowSeconds = Some(600L),
    countEventType = None,
    countThreshold = None,
    triggerEventType = None,
    distinctField = Some("account_id"),
    distinctThreshold = Some(4L),
    filterEventType = Some("login_failure"),
    logField = None,
    policyField = None,
    comparisonOp = None,
  )

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("distinct-count-boundary-check").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    import spark.implicits._

    var results = Vector.empty[(String, Boolean, Boolean)] // (name, expectedAlert, actualAlert)

    // Case 1: exactly threshold-1 (3) distinct accounts against one host,
    // all within the window — must NOT alert. An approximation that
    // over-counts by even 1 at this small a cardinality would fire here.
    val justBelow = Seq(
      Event("e1", "2026-01-01T00:00:00.000Z", "acct-a", "login_failure", "host-1"),
      Event("e2", "2026-01-01T00:00:10.000Z", "acct-b", "login_failure", "host-1"),
      Event("e3", "2026-01-01T00:00:20.000Z", "acct-c", "login_failure", "host-1"),
    ).toDF()
    val belowAlerted = RuleCompiler.compile(spraySpec, justBelow).filter($"groupKey" === "host-1").count() > 0
    results :+= (("3 distinct accounts, threshold 4 -> no alert", false, belowAlerted))

    // Case 2: exactly threshold (4) distinct accounts — must alert.
    val atThreshold = Seq(
      Event("e1", "2026-01-01T00:00:00.000Z", "acct-a", "login_failure", "host-2"),
      Event("e2", "2026-01-01T00:00:10.000Z", "acct-b", "login_failure", "host-2"),
      Event("e3", "2026-01-01T00:00:20.000Z", "acct-c", "login_failure", "host-2"),
      Event("e4", "2026-01-01T00:00:30.000Z", "acct-d", "login_failure", "host-2"),
    ).toDF()
    val atAlerted = RuleCompiler.compile(spraySpec, atThreshold).filter($"groupKey" === "host-2").count() > 0
    results :+= (("4 distinct accounts, threshold 4 -> alert", true, atAlerted))

    // Case 3: only 3 DISTINCT accounts, but 7 total events (duplicates
    // repeated) — must NOT alert. A count(*) instead of a true distinct
    // count would wrongly reach 7 >= 4 and fire; this is the case an
    // exact distinct implementation has to get right in the OTHER
    // direction from case 1.
    val duplicates = Seq(
      Event("e1", "2026-01-01T00:00:00.000Z", "acct-a", "login_failure", "host-3"),
      Event("e2", "2026-01-01T00:00:05.000Z", "acct-a", "login_failure", "host-3"),
      Event("e3", "2026-01-01T00:00:10.000Z", "acct-b", "login_failure", "host-3"),
      Event("e4", "2026-01-01T00:00:15.000Z", "acct-b", "login_failure", "host-3"),
      Event("e5", "2026-01-01T00:00:20.000Z", "acct-c", "login_failure", "host-3"),
      Event("e6", "2026-01-01T00:00:25.000Z", "acct-c", "login_failure", "host-3"),
      Event("e7", "2026-01-01T00:00:30.000Z", "acct-a", "login_failure", "host-3"),
    ).toDF()
    val dupAlerted = RuleCompiler.compile(spraySpec, duplicates).filter($"groupKey" === "host-3").count() > 0
    results :+= (("3 distinct accounts across 7 duplicate-laden events, threshold 4 -> no alert", false, dupAlerted))

    println("=== Distinct-count boundary check (exact count, not approx_count_distinct) ===")
    var allPassed = true
    val caseResults = results.map { case (name, expected, actual) =>
      val ok = expected == actual
      if (!ok) allPassed = false
      println(s"${if (ok) "OK  " else "FAIL"} $name (expected=$expected actual=$actual)")
      s"""{"case": ${escapeJson(name)}, "expectedAlert": $expected, "actualAlert": $actual, "passed": $ok}"""
    }
    println(s"\n${if (allPassed) "ALL PASSED" else "FAILED"}")

    val repoRoot = new java.io.File(".").getCanonicalPath
    val outPath = s"$repoRoot/experiments/results/distinct_count_boundary_check.json"
    val pw = new java.io.PrintWriter(outPath)
    try pw.write(s"""{"allPassed": $allPassed, "cases": [${caseResults.mkString(", ")}]}\n""") finally pw.close()
    println(s"Wrote $outPath")

    spark.stop()
    if (!allPassed) System.exit(1)
  }

  private def escapeJson(s: String): String =
    "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
}
