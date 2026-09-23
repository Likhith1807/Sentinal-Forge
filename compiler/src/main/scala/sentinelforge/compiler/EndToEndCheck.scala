package sentinelforge.compiler

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

/** The check an independent review correctly identified as missing
  * (2026-09-16): every prior ReplayCheck/StreamingCheck/RobustnessCheck/
  * ThroughputCheck result loaded a HAND-AUTHORED compiled spec from
  * data/samples/ir/compiled/ — none of them proved that a spec produced by
  * real NLP extraction, validated by Stage 3, and bridged by
  * compiler/src/spec_bridge.py actually compiles to a correct detection.
  * This does exactly that.
  *
  * CORRECTION, round 2 (same reviewer, same day): the first version of
  * this file read from a fixed shared directory keyed by predicted
  * behaviourId — vulnerable to exactly the staleness/collision problem
  * compiler/test/evaluate_end_to_end.py's round-2 fix addresses on the
  * Python side. This now reads that script's manifest.json from its
  * timestamped run directory (via LATEST_SUCCESSFUL_RUN.txt, or an
  * explicit run id passed as the first argument), and maps
  * report -> behaviourId -> labels file explicitly through the manifest
  * rather than assuming a naming convention.
  */
object EndToEndCheck {

  private val LABELS_BY_BEHAVIOUR = Map(
    "repeated-failed-login-then-success" -> "login_brute_force_labels.json",
    "password-spray-across-accounts" -> "password_spray_labels.json",
    "multi-host-authentication" -> "concurrent_sessions_labels.json",
    "auth-method-policy-violation" -> "service_account_auth_labels.json",
    "mfa-missing-on-required-account" -> "mfa_bypass_labels.json",
  )
  private val ALERT_BEHAVIOURS = Set("repeated-failed-login-then-success", "password-spray-across-accounts", "multi-host-authentication")

  def main(args: Array[String]): Unit = {
    val repoRoot = new java.io.File(".").getCanonicalPath
    val baseDir = s"$repoRoot/data/samples/ir/compiled_from_extraction"

    val runId = if (args.nonEmpty) args(0) else {
      val pointer = new java.io.File(s"$baseDir/LATEST_SUCCESSFUL_RUN.txt")
      if (!pointer.exists()) {
        println(s"$pointer does not exist — run compiler/test/evaluate_end_to_end.py first " +
          "(it only writes this pointer after every required report succeeds).")
        System.exit(1)
      }
      scala.io.Source.fromFile(pointer).mkString.trim
    }
    val runDir = s"$baseDir/$runId"
    val manifestFile = new java.io.File(s"$runDir/manifest.json")
    if (!manifestFile.exists()) {
      println(s"$manifestFile does not exist for run '$runId'.")
      System.exit(1)
    }

    val spark = SparkSession.builder().appName("end-to-end-check").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    // manifest.json is small and flat — read via Spark's own JSON reader
    // rather than adding a JSON library dependency, same approach as
    // CompiledSpec.load.
    val manifestDf = spark.read.option("multiLine", "true").json(manifestFile.getAbsolutePath)
    val status = manifestDf.select("status").first().getString(0)
    if (status != "SUCCESS") {
      println(s"Run '$runId' has status '$status', not SUCCESS — refusing to treat it as a complete run.")
      spark.stop()
      System.exit(1)
    }
    val reportsStruct = manifestDf.select("reports").first().getAs[org.apache.spark.sql.Row](0)
    val reportNames = reportsStruct.schema.fieldNames

    println(s"Using run '$runId' (${reportNames.length} reports in manifest)")

    val events = spark.read.parquet(s"$repoRoot/data/processed/events")
    val policy = spark.read.option("multiLine", "true").json(s"$repoRoot/data/samples/schema/account_policy_reference.json")
      .select(explode(col("records")).as("r")).select("r.*")

    val results = scala.collection.mutable.ArrayBuffer[ReplayCheck.CaseResult]()

    for (reportName <- reportNames) {
      val entry = reportsStruct.getAs[org.apache.spark.sql.Row](reportName)
      val behaviourId = entry.getAs[String]("behaviourId")
      val specPath = s"$repoRoot/${entry.getAs[String]("compiledSpecPath")}"
      val labelsFile = LABELS_BY_BEHAVIOUR.getOrElse(behaviourId,
        throw new IllegalStateException(s"No labels file mapping for behaviourId '$behaviourId' (report '$reportName')"))
      val labelsPath = s"$repoRoot/data/samples/replay/$labelsFile"

      val spec = CompiledSpec.load(spark, specPath)
      if (ALERT_BEHAVIOURS.contains(behaviourId)) {
        results ++= checkAlerts(behaviourId, spec, events, labelsPath, spark)
      } else {
        results ++= checkStatus(behaviourId, spec, events, policy, labelsPath, spark)
      }
    }

    println("\n=== END-TO-END check: real extraction -> real Stage 3 -> real bridge -> real compile -> real execute ===")
    results.foreach { r =>
      val marker = if (r.pass) "OK " else "!! "
      println(f"$marker ${r.behaviourId}%-40s ${r.scenarioId}%-18s expected=${r.expected}%-10s actual=${r.actual}")
    }
    val passCount = results.count(_.pass)
    println(s"\n$passCount / ${results.length} scenarios passed — run '$runId', specs derived from real extraction, not hand-authored.")

    val outPath = s"$repoRoot/experiments/results/phase_end_to_end_replay_check.json"
    val json = results.map { r =>
      s"""{"behaviourId":"${r.behaviourId}","scenarioId":"${r.scenarioId}","expected":"${r.expected}","actual":"${r.actual}","pass":${r.pass}}"""
    }.mkString("[\n  ", ",\n  ", "\n]\n")
    val pw = new java.io.PrintWriter(outPath, "UTF-8")
    try pw.write(s"""{"runId":"$runId","results":$json}""") finally pw.close()
    println(s"Wrote $outPath")

    spark.stop()
    if (passCount != results.length) System.exit(1)
  }

  private def checkAlerts(behaviourId: String, spec: CompiledSpec, events: DataFrame,
                           labelsPath: String, spark: SparkSession): Seq[ReplayCheck.CaseResult] = {
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
      ReplayCheck.CaseResult(behaviourId, scenarioId, expectedAlert.toString, actualAlert.toString, expectedAlert == actualAlert)
    }
  }

  private def checkStatus(behaviourId: String, spec: CompiledSpec, events: DataFrame, policy: DataFrame,
                           labelsPath: String, spark: SparkSession): Seq[ReplayCheck.CaseResult] = {
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
      ReplayCheck.CaseResult(behaviourId, scenarioId, expectedStatus, actualStatus, expectedStatus == actualStatus)
    }
  }
}
