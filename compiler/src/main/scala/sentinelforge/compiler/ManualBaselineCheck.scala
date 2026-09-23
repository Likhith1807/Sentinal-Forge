package sentinelforge.compiler

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._
import sentinelforge.baselines.manual._

/** Same real-data, real-labels check as ReplayCheck, run against the
  * Phase 1 manual baseline's own hand-written Scala objects instead of the
  * Stage 4 compiler's output — so "manual" and "SENTINEL Forge full" are
  * compared on the literal same harness, not assumed equivalent because
  * one informed the other's design.
  */
object ManualBaselineCheck {

  def main(args: Array[String]): Unit = {
    implicit val spark: SparkSession = SparkSession.builder().appName("manual-baseline-check").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath

    val events = spark.read.parquet(s"$repoRoot/data/processed/events")
    val policy = spark.read.option("multiLine", "true").json(s"$repoRoot/data/samples/schema/account_policy_reference.json")
      .select(explode(col("records")).as("r")).select("r.*")

    val results = scala.collection.mutable.ArrayBuffer[ReplayCheck.CaseResult]()

    results ++= checkAlerts("repeated-failed-login-then-success",
      RepeatedFailedLoginThenSuccess.detect(spark, events).withColumnRenamed("account_id", "groupKey"),
      s"$repoRoot/data/samples/replay/login_brute_force_labels.json")

    results ++= checkAlerts("password-spray-across-accounts",
      PasswordSprayAcrossAccounts.detect(spark, events).withColumnRenamed("source_host", "groupKey"),
      s"$repoRoot/data/samples/replay/password_spray_labels.json")

    results ++= checkAlerts("multi-host-authentication",
      ConcurrentSessionsDifferentHosts.detect(spark, events).withColumnRenamed("account_id", "groupKey"),
      s"$repoRoot/data/samples/replay/concurrent_sessions_labels.json")

    results ++= checkStatus("auth-method-policy-violation",
      ServiceAccountInteractiveAuth.detect(spark, events, policy),
      s"$repoRoot/data/samples/replay/service_account_auth_labels.json")

    results ++= checkStatus("mfa-missing-on-required-account",
      MfaBypassOnRequiredAccount.detect(spark, events, policy),
      s"$repoRoot/data/samples/replay/mfa_bypass_labels.json")

    println("\n=== Manual baseline replay check ===")
    results.foreach { r =>
      val marker = if (r.pass) "OK " else "!! "
      println(f"$marker ${r.behaviourId}%-40s ${r.scenarioId}%-18s expected=${r.expected}%-10s actual=${r.actual}")
    }
    val passCount = results.count(_.pass)
    println(s"\n$passCount / ${results.length} scenarios passed")

    val outPath = s"$repoRoot/experiments/results/phase5_manual_baseline_check.json"
    val json = results.map { r =>
      s"""{"behaviourId":"${r.behaviourId}","scenarioId":"${r.scenarioId}","expected":"${r.expected}","actual":"${r.actual}","pass":${r.pass}}"""
    }.mkString("[\n  ", ",\n  ", "\n]\n")
    val pw = new java.io.PrintWriter(outPath, "UTF-8")
    try pw.write(json) finally pw.close()
    println(s"Wrote $outPath")

    spark.stop()
  }

  private def checkAlerts(behaviourId: String, alertDf: DataFrame, labelsPath: String)
                          (implicit spark: SparkSession): Seq[ReplayCheck.CaseResult] = {
    val alertedGroupKeys = alertDf.select("groupKey").collect().map(_.getString(0)).toSet
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

  private def checkStatus(behaviourId: String, statusDf: DataFrame, labelsPath: String)
                          (implicit spark: SparkSession): Seq[ReplayCheck.CaseResult] = {
    val statusByEventId = statusDf.select("event_id", "status").collect()
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
