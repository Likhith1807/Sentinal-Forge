package sentinelforge.compiler

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

/** Runs the 5 compiled specs through the real RuleCompiler over a generated
  * dataset (scripts/datagen: partitioned Parquet events, policy.json,
  * labels.jsonl) and compares the compiler's output to the dataset's labels.
  *
  * The labels come from how each incident was constructed, and were
  * separately cross-checked by an independent pure-Python reference detector
  * -- so agreement here means the Spark compiler agrees with two things that
  * share no code with it. Judged in BOTH directions:
  *
  *   - every label must be satisfied (an expected alert fires with the right
  *     key/status; an expected no_alert produces nothing for its entities);
  *   - every non-`no_alert` output must be expected, which is what shows the
  *     benign background (tens of millions of events) raised nothing.
  *
  * Alert keys: B1/B4/B5 are matched on `triggeringEventId` (+ status).
  * B2/B3 are matched on `groupKey`, because `DistinctCountWithinWindow`
  * emits one row per group without a triggering event (see
  * docs/spec/detection-semantics.md); every generated incident uses its own
  * groupKey, so that recipe's known one-alert-per-group collapse cannot
  * affect the result.
  *
  * Usage: sbt -Dsf.heap=10g "runMain sentinelforge.compiler.GeneratedDataCheck \
  *          --dataset data/generated/scale_1gb [--out <json>] [--spark-tmp <dir>]"
  */
object GeneratedDataCheck {

  private val B1 = "repeated-failed-login-then-success"
  private val B2 = "password-spray-across-accounts"
  private val B3 = "concurrent-sessions-different-hosts"
  private val B4 = "service-account-interactive-auth"
  private val B5 = "mfa-bypass-on-required-account"
  private val behaviours = Seq(B1, B2, B3, B4, B5)
  private val MaxCollected = 1000000

  final case class Label(incidentId: String, behaviourId: String, kind: String, expectedStatus: String,
                         groupKeys: Vector[String], trigger: Option[String], eventIds: Vector[String])

  private def keyedByEvent(behaviourId: String): Boolean = behaviourId != B2 && behaviourId != B3

  private def expectedKey(l: Label): Option[String] =
    if (l.expectedStatus == "no_alert") None
    else if (keyedByEvent(l.behaviourId)) l.trigger
    else l.groupKeys.headOption

  private def js(s: String): String = "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

  def main(args: Array[String]): Unit = {
    val opts = args.sliding(2, 2).collect { case Array(k, v) if k.startsWith("--") => k.drop(2) -> v }.toMap
    val datasetDir = opts.getOrElse("dataset", sys.error("--dataset <dir> is required"))
    val repoRoot = new java.io.File(".").getCanonicalPath
    val outPath = opts.getOrElse("out", s"$repoRoot/experiments/results/phaseB_generated_dataset_check.json")

    val builder = SparkSession.builder()
      .appName("sentinel-forge-generated-data-check")
      .master("local[*]")
      .config("spark.sql.shuffle.partitions", opts.getOrElse("shuffle-partitions", "64"))
      .config("spark.sql.autoBroadcastJoinThreshold", (256L * 1024 * 1024).toString)
    opts.get("spark-tmp").foreach(d => builder.config("spark.local.dir", d))
    val spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    val manifest = spark.read.option("multiLine", "true").json(s"$datasetDir/manifest.json").first()
    val eventsRoot = manifest.getAs[String]("eventsRoot")
    val events = spark.read.parquet(eventsRoot)
    val policy = spark.read.option("multiLine", "true").json(s"$datasetDir/policy.json")
      .select(explode(col("records")).as("r")).select("r.*")

    val labels: Vector[Label] = spark.read.json(s"$datasetDir/labels.jsonl").collect().toVector.map { r =>
      def seq(name: String) = r.getSeq[String](r.fieldIndex(name)).toVector
      Label(r.getAs[String]("incidentId"), r.getAs[String]("behaviourId"), r.getAs[String]("kind"),
        r.getAs[String]("expectedStatus"), seq("groupKeys"),
        Option(r.getAs[String]("expectedTriggerEventId")), seq("eventIds"))
    }

    val eventCount = events.count()
    println(s"Dataset $datasetDir: $eventCount events, ${labels.length} labelled incidents")

    val perBehaviour = behaviours.map { id =>
      val started = System.nanoTime()
      val spec = CompiledSpec.load(spark, s"$repoRoot/data/samples/ir/compiled/$id.compiled.json")
      val (actual, truncated) = collectAlerts(spec, events, policy)
      val seconds = (System.nanoTime() - started) / 1e9

      val mine = labels.filter(_.behaviourId == id)
      val actualKeys = actual.map(_._1)
      val expectedSet = mine.flatMap(l => expectedKey(l).map(k => (k, l.expectedStatus))).toSet

      def satisfied(l: Label): Boolean = l.expectedStatus match {
        case "no_alert" =>
          val keys = if (keyedByEvent(id)) l.eventIds else l.groupKeys
          !keys.exists(actualKeys.contains)
        case status => expectedKey(l).exists(k => actual.contains((k, status)))
      }
      val results = mine.map(l => l -> satisfied(l))
      val unexpected = actual -- expectedSet
      val missing = expectedSet -- actual
      println(f"  $id%-40s ${results.count(_._2)}%6d / ${mine.length}%-6d labels  unexpected=${unexpected.size} " +
        f"missing=${missing.size}  ${seconds}%.1fs" + (if (truncated) "  TRUNCATED" else ""))
      (id, results, unexpected, missing, truncated, seconds)
    }

    val allPass = perBehaviour.forall { case (_, res, unexpected, missing, truncated, _) =>
      res.forall(_._2) && unexpected.isEmpty && missing.isEmpty && !truncated
    }
    println(s"\nOVERALL: ${if (allPass) "PASS" else "FAIL"}")

    val behaviourJson = perBehaviour.map { case (id, res, unexpected, missing, truncated, seconds) =>
      val byKind = res.groupBy(_._1.kind).toSeq.sortBy(_._1).map { case (kind, rs) =>
        s"""{"kind":${js(kind)},"total":${rs.length},"passed":${rs.count(_._2)}}"""
      }.mkString("[", ",", "]")
      val failures = res.filterNot(_._2).take(5).map(r => js(r._1.incidentId)).mkString("[", ",", "]")
      s"""{"behaviourId":${js(id)},"labels":${res.length},"passed":${res.count(_._2)},""" +
        s""""unexpectedAlerts":${unexpected.size},"missingAlerts":${missing.size},"truncated":$truncated,""" +
        s""""seconds":${"%.1f".format(seconds)},"byKind":$byKind,"firstFailedIncidents":$failures}"""
    }.mkString("[\n    ", ",\n    ", "\n  ]")

    val json =
      s"""{
         |  "dataset": ${js(datasetDir.replace("\\", "/"))},
         |  "seed": ${manifest.getAs[Long]("seed")},
         |  "events": $eventCount,
         |  "incidents": ${labels.length},
         |  "backgroundKind": ${js(manifest.getAs[String]("backgroundKind"))},
         |  "sparkVersion": ${js(spark.version)},
         |  "maxHeapMB": ${Runtime.getRuntime.maxMemory / (1024 * 1024)},
         |  "overallPass": $allPass,
         |  "behaviours": $behaviourJson
         |}
         |""".stripMargin
    val pw = new java.io.PrintWriter(outPath, "UTF-8")
    try pw.write(json) finally pw.close()
    println(s"Wrote $outPath")

    spark.stop()
    if (!allPass) System.exit(1)
  }

  /** Non-`no_alert` compiler output as a set of (key, status), bounded so a runaway rule cannot exhaust the driver. */
  private def collectAlerts(spec: CompiledSpec, events: DataFrame, policy: DataFrame): (Set[(String, String)], Boolean) = {
    val projected = spec.recipe match {
      case "PolicyCompare" =>
        RuleCompiler.compile(spec, events, Some(policy)).filter(col("status") =!= "no_alert")
          .select(col("triggeringEventId").as("k"), col("status"))
      case "SequenceThenTrigger" =>
        RuleCompiler.compile(spec, events).select(col("triggeringEventId").as("k"), col("status"))
      case _ =>
        RuleCompiler.compile(spec, events).select(col("groupKey").as("k"), col("status"))
    }
    val rows = projected.limit(MaxCollected + 1).collect()
    (rows.take(MaxCollected).map(r => (r.getString(0), r.getString(1))).toSet, rows.length > MaxCollected)
  }
}
