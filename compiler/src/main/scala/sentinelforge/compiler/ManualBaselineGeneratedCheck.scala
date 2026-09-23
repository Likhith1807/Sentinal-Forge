package sentinelforge.compiler

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._
import sentinelforge.baselines.manual._

/** Phase E item 18: the manual-rules baseline (Phase 1's "no tooling at all" comparison point,
  * `experiments/baselines/manual/`), scored against the same generated dataset and labels
  * `GeneratedDataCheck` uses for SENTINEL Forge — same harness, same real data, real scale.
  *
  * Column shapes differ per hand-written detector (unlike the compiler's uniform output), so each
  * is adapted to a common (groupKey, triggeringEventId, status) projection here rather than
  * changing the baseline code itself — the baseline is supposed to be exactly what Phase 1 wrote.
  *
  * `password-spray-across-accounts`'s hand-written detector never exposes which event triggered
  * it (no event_id in its own output at all) — matched on groupKey only, a real, small quality
  * gap in the hand-written baseline itself worth naming, not silently worked around. It also still
  * calls `approx_count_distinct` (the exact approximation the real compiler moved away from after
  * the independent review — `docs/spec/independent-review-corrections.md`); at real scale, unlike
  * the original 48-event replay set, this is large enough for that estimator's error to actually
  * be visible, which this check reports on directly rather than assuming is still negligible.
  *
  * Usage: sbt -Dsf.heap=10g "runMain sentinelforge.compiler.ManualBaselineGeneratedCheck --dataset data/generated/scale_1gb [--out <json>]"
  */
object ManualBaselineGeneratedCheck {

  private val B1 = "repeated-failed-login-then-success"
  private val B2 = "password-spray-across-accounts"
  private val B3 = "multi-host-authentication"
  private val B4 = "auth-method-policy-violation"
  private val B5 = "mfa-missing-on-required-account"
  private val MaxCollected = 1000000

  private def js(s: String): String = "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

  def main(args: Array[String]): Unit = {
    val opts = args.sliding(2, 2).collect { case Array(k, v) if k.startsWith("--") => k.drop(2) -> v }.toMap
    val datasetDir = opts.getOrElse("dataset", sys.error("--dataset <dir> is required"))
    val repoRoot = new java.io.File(".").getCanonicalPath
    val outPath = opts.getOrElse("out", s"$repoRoot/experiments/results/phaseE_manual_baseline_at_scale.json")

    val builder = SparkSession.builder().appName("sentinel-forge-manual-baseline-at-scale").master("local[*]")
      .config("spark.sql.shuffle.partitions", opts.getOrElse("shuffle-partitions", "64"))
    opts.get("spark-tmp").foreach(d => builder.config("spark.local.dir", d))
    implicit val spark: SparkSession = builder.getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    val manifest = spark.read.option("multiLine", "true").json(s"$datasetDir/manifest.json").first()
    val events = spark.read.parquet(manifest.getAs[String]("eventsRoot"))
    val policy = spark.read.option("multiLine", "true").json(s"$datasetDir/policy.json")
      .select(explode(col("records")).as("r")).select("r.*")

    case class Label(incidentId: String, behaviourId: String, kind: String, expectedStatus: String,
                     groupKeys: Vector[String], trigger: Option[String], eventIds: Vector[String])
    val labels: Vector[Label] = spark.read.json(s"$datasetDir/labels.jsonl").collect().toVector.map { r =>
      def seq(name: String) = r.getSeq[String](r.fieldIndex(name)).toVector
      Label(r.getAs[String]("incidentId"), r.getAs[String]("behaviourId"), r.getAs[String]("kind"),
        r.getAs[String]("expectedStatus"), seq("groupKeys"),
        Option(r.getAs[String]("expectedTriggerEventId")), seq("eventIds"))
    }

    // Each entry: (groupKeyCol, triggerEventIdCol option, statusCol option, the raw DataFrame).
    // `None` for triggerEventIdCol means "match on groupKey only" — the real gap noted above.
    def project(behaviourId: String, df: DataFrame, groupCol: String,
               triggerCol: Option[String], statusCol: Option[String]): (String, DataFrame) = {
      val base = df.withColumnRenamed(groupCol, "groupKey")
      val withTrigger = triggerCol.map(c => base.withColumnRenamed(c, "triggeringEventId")).getOrElse(base.withColumn("triggeringEventId", lit(null: String)))
      val withStatus = statusCol.map(c => withTrigger.withColumnRenamed(c, "status")).getOrElse(withTrigger.withColumn("status", lit("alert")))
      behaviourId -> withStatus.select("groupKey", "triggeringEventId", "status")
    }

    val started1 = System.nanoTime()
    val b1 = project(B1, RepeatedFailedLoginThenSuccess.detect(spark, events), "account_id", Some("triggering_success_event_id"), None)
    val b1Count = b1._2.count(); val b1Secs = (System.nanoTime() - started1) / 1e9

    val started2 = System.nanoTime()
    val b2Raw = PasswordSprayAcrossAccounts.detect(spark, events)
    val approxDistinct = b2Raw.select("distinct_accounts").collect().map(_.getLong(0))
    val b2 = project(B2, b2Raw, "source_host", None, None)
    val b2Count = b2._2.count(); val b2Secs = (System.nanoTime() - started2) / 1e9

    val started3 = System.nanoTime()
    val b3 = project(B3, ConcurrentSessionsDifferentHosts.detect(spark, events), "account_id", Some("triggering_event_id"), None)
    val b3Count = b3._2.count(); val b3Secs = (System.nanoTime() - started3) / 1e9

    val started4 = System.nanoTime()
    val b4 = project(B4, ServiceAccountInteractiveAuth.detect(spark, events, policy), "account_id", Some("event_id"), Some("status"))
    val b4Count = b4._2.filter(col("status") =!= "no_alert").count(); val b4Secs = (System.nanoTime() - started4) / 1e9

    val started5 = System.nanoTime()
    val b5 = project(B5, MfaBypassOnRequiredAccount.detect(spark, events, policy), "account_id", Some("event_id"), Some("status"))
    val b5Count = b5._2.filter(col("status") =!= "no_alert").count(); val b5Secs = (System.nanoTime() - started5) / 1e9

    val projections = Seq(b1, b2, b3,
      B4 -> b4._2.filter(col("status") =!= "no_alert"),
      B5 -> b5._2.filter(col("status") =!= "no_alert"))
    val timings = Map(B1 -> b1Secs, B2 -> b2Secs, B3 -> b3Secs, B4 -> b4Secs, B5 -> b5Secs)

    val perBehaviour = projections.map { case (id, df) =>
      val rows = df.limit(MaxCollected + 1).collect()
      val truncated = rows.length > MaxCollected
      val actual = rows.take(MaxCollected).map(r => (r.getString(0), r.getString(2))).toSet
      val actualEventIds = rows.take(MaxCollected).flatMap(r => Option(r.getString(1))).toSet
      val actualGroupKeys = rows.take(MaxCollected).map(_.getString(0)).toSet

      val mine = labels.filter(_.behaviourId == id)
      def satisfied(l: Label): Boolean = l.expectedStatus match {
        case "no_alert" => if (id == B2) !l.groupKeys.exists(actualGroupKeys.contains) else !l.eventIds.exists(actualEventIds.contains)
        case status if id == B2 => l.groupKeys.exists(k => actual.contains((k, status)))
        case status => l.trigger.exists(t => actualEventIds.contains(t) && rows.exists(r => r.getString(1) == t && r.getString(2) == status))
      }
      val results = mine.map(l => l -> satisfied(l))
      (id, results, truncated, timings(id))
    }

    println(s"\n=== Manual baseline at scale ($datasetDir) ===")
    perBehaviour.foreach { case (id, res, truncated, secs) =>
      println(f"  $id%-40s ${res.count(_._2)}%6d / ${res.length}%-6d labels  ${secs}%.1fs" + (if (truncated) "  TRUNCATED" else ""))
    }
    println(s"\nB2 (password-spray) approx_count_distinct sample values (first 10): ${approxDistinct.take(10).mkString(", ")}")
    println("(the real compiler's threshold check uses an EXACT distinct count since the independent-review fix; " +
      "this hand-written baseline still uses Spark's approximate one, unchanged from Phase 1 — comparing these " +
      "against the dataset's true distinct-account counts per incident is what would surface real estimation error.)")

    val allPass = perBehaviour.forall { case (_, res, truncated, _) => res.forall(_._2) && !truncated }

    val json = perBehaviour.map { case (id, res, truncated, secs) =>
      s"""{"behaviourId":${js(id)},"labels":${res.length},"passed":${res.count(_._2)},"truncated":$truncated,"seconds":${"%.1f".format(secs)}}"""
    }.mkString("[\n    ", ",\n    ", "\n  ]")
    val outJson =
      s"""{
         |  "dataset": ${js(datasetDir.replace("\\", "/"))},
         |  "overallPass": $allPass,
         |  "behaviours": $json,
         |  "b2ApproxDistinctSample": [${approxDistinct.take(20).mkString(",")}],
         |  "note": "password-spray-across-accounts is matched on groupKey only -- the hand-written baseline exposes no triggering event id."
         |}
         |""".stripMargin
    val pw = new java.io.PrintWriter(outPath, "UTF-8")
    try pw.write(outJson) finally pw.close()
    println(s"\nWrote $outPath")

    spark.stop()
  }
}
