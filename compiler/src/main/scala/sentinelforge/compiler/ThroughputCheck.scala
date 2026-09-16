package sentinelforge.compiler

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.functions._

/** Phase 5's throughput/p95 latency measurement, scoped honestly: this
  * dataset (48 events) is too small for a number that means anything at
  * production scale — reported as exactly what it is, a repeated-query
  * latency measurement on the real dataset that exists, not a claim about
  * larger-scale performance no data here could support.
  */
object ThroughputCheck {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("throughput-check").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath

    val events = spark.read.parquet(s"$repoRoot/data/processed/events").cache()
    val eventCount = events.count() // materialize the cache before timing
    val policy = spark.read.option("multiLine", "true").json(s"$repoRoot/data/samples/schema/account_policy_reference.json")
      .select(explode(col("records")).as("r")).select("r.*").cache()
    policy.count()

    val specs = Seq(
      ("repeated-failed-login-then-success", None),
      ("password-spray-across-accounts", None),
      ("concurrent-sessions-different-hosts", None),
      ("service-account-interactive-auth", Some(policy)),
      ("mfa-bypass-on-required-account", Some(policy)),
    ).map { case (id, pol) => (CompiledSpec.load(spark, s"$repoRoot/data/samples/ir/compiled/$id.compiled.json"), pol) }

    val runsPerSpec = 10
    val allLatenciesMs = scala.collection.mutable.ArrayBuffer[Double]()
    val perBehaviour = scala.collection.mutable.Map[String, Seq[Double]]()

    for ((spec, pol) <- specs) {
      val latencies = (1 to runsPerSpec).map { _ =>
        val start = System.nanoTime()
        RuleCompiler.compile(spec, events, pol).count()
        (System.nanoTime() - start) / 1e6
      }
      perBehaviour(spec.behaviourId) = latencies
      allLatenciesMs ++= latencies
    }

    val sorted = allLatenciesMs.sorted
    val p50 = sorted(sorted.length / 2)
    val p95 = sorted((sorted.length * 0.95).toInt.min(sorted.length - 1))
    val meanMs = allLatenciesMs.sum / allLatenciesMs.length
    val throughputEventsPerSec = eventCount / (meanMs / 1000.0)

    println(s"=== Throughput / latency (n=$eventCount events, $runsPerSpec runs per behaviour, local[*] on this machine) ===")
    for ((behaviourId, latencies) <- perBehaviour) {
      println(f"  $behaviourId%-42s mean=${latencies.sum / latencies.length}%.1fms  min=${latencies.min}%.1fms  max=${latencies.max}%.1fms")
    }
    println(f"\nOverall: mean=${meanMs}%.1fms  p50=${p50}%.1fms  p95=${p95}%.1fms")
    println(f"Approx throughput at this scale: ${throughputEventsPerSec}%.0f events/sec (48-event dataset — not a production-scale claim)")

    val json =
      s"""{
         |  "datasetEventCount": $eventCount,
         |  "runsPerBehaviour": $runsPerSpec,
         |  "meanLatencyMs": ${meanMs},
         |  "p50LatencyMs": ${p50},
         |  "p95LatencyMs": ${p95},
         |  "approxThroughputEventsPerSec": ${throughputEventsPerSec},
         |  "caveat": "Measured on a 48-event dataset on one local machine (local[*]) - a real number for the data that exists, not a production-scale benchmark."
         |}
         |""".stripMargin
    val pw = new java.io.PrintWriter(s"$repoRoot/experiments/results/phase5_throughput_check.json")
    try pw.write(json) finally pw.close()
    println(s"\nWrote $repoRoot/experiments/results/phase5_throughput_check.json")

    spark.stop()
  }
}
