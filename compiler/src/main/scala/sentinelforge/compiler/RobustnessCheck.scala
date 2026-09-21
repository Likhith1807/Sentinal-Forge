package sentinelforge.compiler

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.functions._
import org.apache.spark.sql.types._

/** Robustness experiment: what happens to SENTINEL Forge's own compiled
  * rules when the data they depend on degrades — not what happens to a
  * baseline (that's Phase 5's baseline-comparison work above). Two real,
  * cheap tests given RuleCompiler already exists:
  *
  *   1. Policy source unavailable (empty policy table) — does a
  *      PolicyCompare rule silently misclassify, or correctly degrade to
  *      insufficient_context for everyone?
  *   2. A required log field goes missing entirely (not just optional-and-empty,
  *      genuinely absent from the schema) — does the rule fail loudly and
  *      specifically, or silently produce wrong output?
  */
object RobustnessCheck {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("robustness-check").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath

    val events = spark.read.parquet(s"$repoRoot/data/processed/events")
    val realPolicy = spark.read.option("multiLine", "true").json(s"$repoRoot/data/samples/schema/account_policy_reference.json")
      .select(explode(col("records")).as("r")).select("r.*")

    println("=== Test 1: policy source unavailable (empty policy table) ===")
    val mfaSpec = CompiledSpec.load(spark, s"$repoRoot/data/samples/ir/compiled/mfa-bypass-on-required-account.compiled.json")

    println("-- baseline (real policy data available) --")
    RuleCompiler.compile(mfaSpec, events, Some(realPolicy))
      .filter(col("groupKey").isin("cfinance", "msmith", "contractor-x9"))
      .select("groupKey", "status").show(false)

    val emptyPolicy = spark.createDataFrame(spark.sparkContext.emptyRDD[org.apache.spark.sql.Row], realPolicy.schema)
    println("-- degraded (policy table empty, as if the policy source were down) --")
    val degraded = RuleCompiler.compile(mfaSpec, events, Some(emptyPolicy))
      .filter(col("groupKey").isin("cfinance", "msmith", "contractor-x9"))
    degraded.select("groupKey", "status").show(false)

    val allInsufficientContext = degraded.select("status").collect().forall(_.getString(0) == "insufficient_context")
    println(s"All 3 accounts correctly degraded to insufficient_context (no false alert, no silent wrong no_alert): $allInsufficientContext")

    println("\n=== Test 2: a required field is missing entirely from the event schema ===")
    val spraySpec = CompiledSpec.load(spark, s"$repoRoot/data/samples/ir/compiled/password-spray-across-accounts.compiled.json")
    val eventsMissingHost = events.drop("source_host")
    println("password-spray-across-accounts needs source_host as its grouping key. Attempting to compile against events with that column dropped entirely:")
    val failureMessage = try {
      RuleCompiler.compile(spraySpec, eventsMissingHost).count()
      "NO EXCEPTION — silently produced output despite the missing column (this would be bad)"
    } catch {
      case e: Exception => s"${e.getClass.getSimpleName}: ${e.getMessage.linesIterator.next()}"
    }
    println(s"Result: $failureMessage")

    val outPath = s"$repoRoot/experiments/results/phase5_robustness_check.json"
    val pw = new java.io.PrintWriter(outPath, "UTF-8")
    try pw.write(
      s"""{
         |  "policySourceUnavailable": {"allDegradedToInsufficientContext": $allInsufficientContext},
         |  "missingRequiredField": {"failsLoudlyAndSpecifically": ${failureMessage.startsWith("org.apache.spark") || failureMessage.contains("AnalysisException")}, "message": ${escapeJson(failureMessage)}}
         |}
         |""".stripMargin) finally pw.close()
    println(s"\nWrote $outPath")

    spark.stop()
  }

  private def escapeJson(s: String): String =
    "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
}
