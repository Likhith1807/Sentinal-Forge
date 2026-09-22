package sentinelforge.compiler

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

/** Phase E item 15: throughput and p95 latency at real scale, with REPEATED runs per behaviour —
  * a single run at scale is exactly the noisy-measurement trap `docs/data-sources.md` already
  * flagged for the 1 GB check (a second run there gave a >2x different number on the same data).
  * This check exists so a scaling claim is never one number from one run.
  *
  * For each of the 5 behaviours, forces full execution (`.count()`) of `RuleCompiler.compile(...)`
  * `--runs` times (default 5) and reports mean/p50/p95/min/max wall-clock time and an approximate
  * throughput (dataset event count / mean seconds) — approximate because the whole dataset is
  * scanned per run regardless of how many events actually match a given recipe's filter, which is
  * the real cost being measured (a rule has to read the data before it can decide what to do with
  * it) rather than the (usually much smaller) alert-producing subset.
  *
  * Usage: sbt -Dsf.heap=10g "runMain sentinelforge.compiler.ScaleBenchmarkCheck --dataset <dir> --runs 5 [--out <json>]"
  */
object ScaleBenchmarkCheck {

  private val allBehaviours = Seq(
    "repeated-failed-login-then-success", "password-spray-across-accounts",
    "concurrent-sessions-different-hosts", "service-account-interactive-auth",
    "mfa-bypass-on-required-account",
  )

  private def percentile(sorted: Seq[Double], p: Double): Double = {
    val idx = math.min(sorted.length - 1, math.ceil(p * sorted.length).toInt - 1).max(0)
    sorted(idx)
  }

  private def js(s: String): String = "\"" + s.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

  def main(args: Array[String]): Unit = {
    val opts = args.sliding(2, 2).collect { case Array(k, v) if k.startsWith("--") => k.drop(2) -> v }.toMap
    val datasetDir = opts.getOrElse("dataset", sys.error("--dataset <dir> is required"))
    val runs = opts.getOrElse("runs", "5").toInt
    val behaviours = opts.get("behaviours").map(_.split(",").toSeq).getOrElse(allBehaviours)
    val repoRoot = new java.io.File(".").getCanonicalPath
    val outPath = opts.getOrElse("out", s"$repoRoot/experiments/results/phaseE_scale_benchmark_${new java.io.File(datasetDir).getName}.json")

    val builder = SparkSession.builder().appName("sentinel-forge-scale-benchmark").master("local[*]")
      .config("spark.sql.shuffle.partitions", opts.getOrElse("shuffle-partitions", "64"))
      .config("spark.sql.autoBroadcastJoinThreshold", (256L * 1024 * 1024).toString)
    opts.get("spark-tmp").foreach(d => builder.config("spark.local.dir", d))
    val spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    val manifest = spark.read.option("multiLine", "true").json(s"$datasetDir/manifest.json").first()
    val eventsRoot = manifest.getAs[String]("eventsRoot")
    val events = spark.read.parquet(eventsRoot)
    events.cache()
    val eventCount = events.count()
    val policy = spark.read.option("multiLine", "true").json(s"$datasetDir/policy.json")
      .select(explode(col("records")).as("r")).select("r.*")
    policy.cache()
    policy.count()
    println(s"Dataset $datasetDir: $eventCount events. Benchmarking $runs run(s) per behaviour.")

    val perBehaviour = behaviours.map { id =>
      val spec = CompiledSpec.load(spark, s"$repoRoot/data/samples/ir/compiled/$id.compiled.json")
      val policyOpt = if (spec.recipe == "PolicyCompare") Some(policy) else None
      val timings = (1 to runs).map { run =>
        val started = System.nanoTime()
        val count = RuleCompiler.compile(spec, events, policyOpt).count()
        val seconds = (System.nanoTime() - started) / 1e9
        println(f"  $id%-40s run $run/$runs: ${seconds}%.2fs (${count} result rows)")
        seconds
      }
      val sorted = timings.sorted
      val mean = timings.sum / timings.length
      val p50 = percentile(sorted, 0.50)
      val p95 = percentile(sorted, 0.95)
      val throughput = eventCount / mean
      (id, mean, p50, p95, sorted.min, sorted.max, throughput, timings)
    }

    println(f"\n=== Scale benchmark summary: $datasetDir ($eventCount events, $runs runs/behaviour) ===")
    perBehaviour.foreach { case (id, mean, p50, p95, min, max, tput, _) =>
      println(f"  $id%-40s mean=${mean}%.2fs p50=${p50}%.2fs p95=${p95}%.2fs min=${min}%.2fs max=${max}%.2fs throughput=${tput}%.0f events/s")
    }

    val json = perBehaviour.map { case (id, mean, p50, p95, min, max, tput, timings) =>
      s"""{"behaviourId":${js(id)},"meanSeconds":$mean,"p50Seconds":$p50,"p95Seconds":$p95,""" +
        s""""minSeconds":$min,"maxSeconds":$max,"throughputEventsPerSec":$tput,"allRunsSeconds":[${timings.mkString(",")}]}"""
    }.mkString("[\n    ", ",\n    ", "\n  ]")
    val outJson =
      s"""{
         |  "dataset": ${js(datasetDir.replace("\\", "/"))},
         |  "events": $eventCount,
         |  "runsPerBehaviour": $runs,
         |  "sparkVersion": ${js(spark.version)},
         |  "maxHeapMB": ${Runtime.getRuntime.maxMemory / (1024 * 1024)},
         |  "behaviours": $json
         |}
         |""".stripMargin
    val pw = new java.io.PrintWriter(outPath, "UTF-8")
    try pw.write(outJson) finally pw.close()
    println(s"\nWrote $outPath")

    spark.stop()
  }
}
