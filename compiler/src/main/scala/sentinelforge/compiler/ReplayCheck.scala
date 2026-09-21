package sentinelforge.compiler

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

/** Runs each of the 5 compiled specs against the real Parquet replay store
  * (data/processed/events, built in Phase 1 by
  * scripts/build_processed_dataset.py) and checks the output against the
  * independently-authored labels in the `_labels.json` files under
  * data/samples/replay —
  * see docs/data-splits.md. This is Stage 4 "Execute" plus a first, real
  * slice of Phase 5's replay evaluation running end-to-end, not a
  * throwaway smoke test.
  */
object ReplayCheck {

  case class CaseResult(behaviourId: String, scenarioId: String, expected: String, actual: String, pass: Boolean)

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder()
      .appName("sentinel-forge-replay-check")
      .master("local[*]")
      .getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    val repoRoot = new java.io.File(".").getCanonicalPath

    val events = spark.read.parquet(s"$repoRoot/data/processed/events")
    val policy = spark.read.option("multiLine", "true").json(s"$repoRoot/data/samples/schema/account_policy_reference.json")
      .select(explode(col("records")).as("r")).select("r.*")

    val results = scala.collection.mutable.ArrayBuffer[CaseResult]()

    results ++= checkAlertBehaviour(spark, repoRoot, "repeated-failed-login-then-success", events,
      s"$repoRoot/data/samples/replay/login_brute_force_labels.json")
    results ++= checkAlertBehaviour(spark, repoRoot, "password-spray-across-accounts", events,
      s"$repoRoot/data/samples/replay/password_spray_labels.json")
    results ++= checkAlertBehaviour(spark, repoRoot, "concurrent-sessions-different-hosts", events,
      s"$repoRoot/data/samples/replay/concurrent_sessions_labels.json")
    results ++= checkStatusBehaviour(spark, repoRoot, "service-account-interactive-auth", events, policy,
      s"$repoRoot/data/samples/replay/service_account_auth_labels.json")
    results ++= checkStatusBehaviour(spark, repoRoot, "mfa-bypass-on-required-account", events, policy,
      s"$repoRoot/data/samples/replay/mfa_bypass_labels.json")

    println("\n=== Phase 4 replay check ===")
    results.foreach { r =>
      val marker = if (r.pass) "OK " else "!! "
      println(f"$marker ${r.behaviourId}%-40s ${r.scenarioId}%-18s expected=${r.expected}%-10s actual=${r.actual}")
    }
    val passCount = results.count(_.pass)
    println(s"\n$passCount / ${results.length} scenarios passed")

    val outPath = s"$repoRoot/experiments/results/phase4_replay_check.json"
    val json = results.map { r =>
      s"""{"behaviourId":"${r.behaviourId}","scenarioId":"${r.scenarioId}","expected":"${r.expected}","actual":"${r.actual}","pass":${r.pass}}"""
    }.mkString("[\n  ", ",\n  ", "\n]\n")
    val pw = new java.io.PrintWriter(outPath, "UTF-8")
    try pw.write(json) finally pw.close()
    println(s"Wrote $outPath")

    spark.stop()
    if (passCount != results.length) System.exit(1)
  }

  /** B1/B2/B3: the spec produces one alert row per triggering groupKey;
    * pass if "did this groupKey alert" matches the label's expectedAlert. */
  private def checkAlertBehaviour(spark: SparkSession, repoRoot: String, behaviourId: String,
                                   events: DataFrame, labelsPath: String): Seq[CaseResult] = {
    val spec = CompiledSpec.load(spark, s"$repoRoot/data/samples/ir/compiled/$behaviourId.compiled.json")
    val alerts = RuleCompiler.compile(spec, events)
    val alertedGroupKeys = alerts.select("groupKey").collect().map(_.getString(0)).toSet

    val labels = spark.read.option("multiLine", "true").json(labelsPath)
      .select(explode(col("scenarios")).as("s")).select("s.*")

    labels.collect().toSeq.map { row =>
      val scenarioId = row.getAs[String]("scenarioId")
      val groupKeyField = if (row.schema.fieldNames.contains("groupKey")) "groupKey" else "accountId"
      val groupKey = row.getAs[String](groupKeyField)
      val expectedAlert = row.getAs[Boolean]("expectedAlert")
      val actualAlert = alertedGroupKeys.contains(groupKey)
      CaseResult(behaviourId, scenarioId, expectedAlert.toString, actualAlert.toString, expectedAlert == actualAlert)
    }
  }

  /** B4/B5: the spec produces a 3-way status per triggering event; pass if
    * the status at that eventId matches the label's expectedStatus. */
  private def checkStatusBehaviour(spark: SparkSession, repoRoot: String, behaviourId: String,
                                    events: DataFrame, policy: DataFrame, labelsPath: String): Seq[CaseResult] = {
    val spec = CompiledSpec.load(spark, s"$repoRoot/data/samples/ir/compiled/$behaviourId.compiled.json")
    val out = RuleCompiler.compile(spec, events, Some(policy))
    val statusByEventId = out.select("triggeringEventId", "status").collect()
      .map(r => r.getString(0) -> r.getString(1)).toMap

    val labels = spark.read.option("multiLine", "true").json(labelsPath)
      .select(explode(col("scenarios")).as("s")).select("s.*")

    labels.collect().toSeq.map { row =>
      val scenarioId = row.getAs[String]("scenarioId")
      val eventId = row.getAs[String]("eventId")
      val expectedStatus = row.getAs[String]("expectedStatus")
      val actualStatus = statusByEventId.getOrElse(eventId, "MISSING")
      CaseResult(behaviourId, scenarioId, expectedStatus, actualStatus, expectedStatus == actualStatus)
    }
  }
}
