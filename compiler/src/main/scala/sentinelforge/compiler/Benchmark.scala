package sentinelforge.compiler

import java.lang.management.ManagementFactory
import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Paths}
import java.util.concurrent.atomic.AtomicLong

import scala.jdk.CollectionConverters._

import com.fasterxml.jackson.databind.ObjectMapper
import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.functions._

/** Batch benchmark that reports OBSERVATIONS, not conclusions.
  *
  *   Benchmark --dataset <dir> [--reps 10] [--warmup 1] [--behaviours a,b] [--cache false] [--stats true]
  *             [--master local[8]] [--shuffle-partitions 64] [--out file.json]
  *
  * What is measured and how (docs/benchmarks.md is the reading guide):
  *  - COLD START: JVM start -> SparkSession ready, and the FIRST query executed in the process, reported separately.
  *  - BATCH COMPLETION: wall-clock of `RuleCompiler.compile(...).count()` for each repetition. With `--cache false`
  *    (the default) every repetition re-reads the Parquet files, so the number includes I/O; `--cache true` measures
  *    an in-memory scan and is labelled as such. Warm-up repetitions are kept in the raw output but flagged.
  *  - MEMORY: heap sampled every 100 ms during each repetition (peak used), plus GC time.
  *  - FAILURE RATE: a repetition that throws (e.g. OutOfMemoryError) is recorded, not dropped.
  *  - DATA SHAPE: event count, distinct keys (approximate, HyperLogLog, stated as such) and skew (largest group's share
  *    and the 50th / 99th percentile group size) for the two grouping columns.
  * Percentiles are NOT computed here: the driver derives only the statistics the repetition count can support.
  */
object Benchmark {
  private val all = Seq("repeated-failed-login-then-success", "password-spray-across-accounts", "multi-host-authentication",
    "auth-method-policy-violation", "mfa-missing-on-required-account")
  private val mapper = new ObjectMapper()

  def main(args: Array[String]): Unit = {
    val o = args.sliding(2, 2).collect { case Array(k, v) if k.startsWith("--") => k.drop(2) -> v }.toMap
    val dataset = o.getOrElse("dataset", sys.error("--dataset <dir> is required"))
    val reps = o.getOrElse("reps", "10").toInt
    val warmup = o.getOrElse("warmup", "1").toInt
    val cache = o.getOrElse("cache", "false").toBoolean
    val behaviours = o.get("behaviours").map(_.split(",").toSeq).getOrElse(all)
    val repoRoot = new java.io.File(".").getCanonicalPath
    val outPath = o.getOrElse("out", s"$repoRoot/experiments/results/benchmarks/batch_${new java.io.File(dataset).getName}.json")

    val jvmStart = ManagementFactory.getRuntimeMXBean.getStartTime
    val builder = SparkSession.builder().appName("sentinel-forge-benchmark").master(o.getOrElse("master", "local[*]"))
      .config("spark.sql.session.timeZone", "UTC").config("spark.ui.enabled", "false")
      .config("spark.sql.shuffle.partitions", o.getOrElse("shuffle-partitions", "64"))
      .config("spark.sql.autoBroadcastJoinThreshold", (256L * 1024 * 1024).toString)
    o.get("spark-tmp").foreach(d => builder.config("spark.local.dir", d))
    val spark = builder.getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    val sessionReadyMs = System.currentTimeMillis() - jvmStart

    val manifest = spark.read.option("multiLine", "true").json(s"$dataset/manifest.json").first()
    val events0 = spark.read.parquet(manifest.getAs[String]("eventsRoot"))
    val policy0 = spark.read.option("multiLine", "true").json(s"$dataset/policy.json").select(explode(col("records")).as("r")).select("r.*")
    val (events, policy) = if (cache) (events0.cache(), policy0.cache()) else (events0, policy0)

    val out = new java.util.LinkedHashMap[String, AnyRef]()
    val t0 = System.nanoTime()
    val eventCount = events.count()                                  // also warms the file listing / footer cache
    val firstScanSeconds = (System.nanoTime() - t0) / 1e9
    if (cache) policy.count()

    val shape = new java.util.LinkedHashMap[String, AnyRef]()
    shape.put("events", java.lang.Long.valueOf(eventCount))
    if (o.getOrElse("stats", "true").toBoolean) {
      Seq("account_id", "source_host").foreach { c =>
        val d = events.agg(approx_count_distinct(col(c), 0.01)).first().getLong(0)
        val sizes = events.groupBy(col(c)).count()
        val r = sizes.agg(max("count"), expr("percentile_approx(count, array(0.5, 0.99))")).first()
        val mx = r.getLong(0)
        val ps = r.getSeq[java.lang.Long](1)
        val m = new java.util.LinkedHashMap[String, AnyRef]()
        m.put("distinctApprox", java.lang.Long.valueOf(d)); m.put("largestGroupEvents", java.lang.Long.valueOf(mx))
        m.put("largestGroupShare", java.lang.Double.valueOf(mx.toDouble / eventCount))
        m.put("groupSizeP50", ps.head); m.put("groupSizeP99", ps(1))
        shape.put(c, m)
      }
    }

    // heap sampler
    val mem = ManagementFactory.getMemoryMXBean
    val peak = new AtomicLong(0L)
    @volatile var sampling = true
    val sampler = new Thread(() => { while (sampling) { peak.updateAndGet(p => math.max(p, mem.getHeapMemoryUsage.getUsed)); Thread.sleep(100) } })
    sampler.setDaemon(true); sampler.start()
    def gcMs: Long = ManagementFactory.getGarbageCollectorMXBeans.asScala.map(_.getCollectionTime).sum

    var firstQueryOfProcess = true
    val results = new java.util.ArrayList[AnyRef]()
    behaviours.foreach { id =>
      val spec = CompiledSpec.load(spark, s"$repoRoot/data/samples/ir/compiled/$id.compiled.json")
      val polOpt = if (spec.recipe == "PolicyCompare") Some(policy) else None
      var consecutiveFailures = 0
      (0 until (reps + warmup)).foreach { i =>
        if (consecutiveFailures < 3) {
          val rec = new java.util.LinkedHashMap[String, AnyRef]()
          rec.put("behaviourId", id); rec.put("rep", Integer.valueOf(i)); rec.put("warmup", java.lang.Boolean.valueOf(i < warmup))
          rec.put("coldProcessFirstQuery", java.lang.Boolean.valueOf(firstQueryOfProcess))
          peak.set(mem.getHeapMemoryUsage.getUsed)
          val g0 = gcMs
          val s = System.nanoTime()
          try {
            val n = RuleCompiler.compile(spec, events, polOpt).count()
            rec.put("seconds", java.lang.Double.valueOf((System.nanoTime() - s) / 1e9))
            rec.put("resultRows", java.lang.Long.valueOf(n)); rec.put("failed", java.lang.Boolean.FALSE)
            consecutiveFailures = 0
          } catch {
            case e: Throwable =>
              rec.put("seconds", java.lang.Double.valueOf((System.nanoTime() - s) / 1e9)); rec.put("failed", java.lang.Boolean.TRUE)
              rec.put("error", e.getClass.getSimpleName + ": " + String.valueOf(e.getMessage).take(160))
              consecutiveFailures += 1
          }
          firstQueryOfProcess = false
          rec.put("peakHeapMB", java.lang.Long.valueOf(peak.get() / (1024 * 1024))); rec.put("gcSeconds", java.lang.Double.valueOf((gcMs - g0) / 1000.0))
          results.add(rec)
          println(s"  $id rep $i: ${rec.get("seconds")} s ${if (rec.get("failed") == java.lang.Boolean.TRUE) "FAILED" else ""}")
        }
      }
    }
    sampling = false

    val rt = ManagementFactory.getRuntimeMXBean
    val env = new java.util.LinkedHashMap[String, AnyRef]()
    env.put("sparkVersion", spark.version); env.put("scalaVersion", scala.util.Properties.versionNumberString)
    env.put("java", System.getProperty("java.version") + " " + System.getProperty("java.vm.name"))
    env.put("maxHeapMB", java.lang.Long.valueOf(Runtime.getRuntime.maxMemory / (1024 * 1024)))
    env.put("master", spark.sparkContext.master); env.put("defaultParallelism", Integer.valueOf(spark.sparkContext.defaultParallelism))
    env.put("availableProcessors", Integer.valueOf(Runtime.getRuntime.availableProcessors()))
    env.put("shufflePartitions", spark.conf.get("spark.sql.shuffle.partitions"))
    env.put("jvmArgs", rt.getInputArguments.asScala.filterNot(_.startsWith("--add-opens")).mkString(" "))
    out.put("environment", env)
    out.put("dataset", dataset.replace("\\", "/")); out.put("datasetShape", shape)
    out.put("mode", if (cache) "in-memory (cached) scan" else "reads Parquet on every repetition")
    out.put("coldStart", Map("jvmToSessionReadySeconds" -> java.lang.Double.valueOf(sessionReadyMs / 1000.0),
      "firstFullScanSeconds" -> java.lang.Double.valueOf(firstScanSeconds)).asJava)
    out.put("repetitionsPerBehaviour", Integer.valueOf(reps)); out.put("warmupPerBehaviour", Integer.valueOf(warmup))
    out.put("observations", results)
    Files.createDirectories(Paths.get(outPath).getParent)
    Files.write(Paths.get(outPath), mapper.writerWithDefaultPrettyPrinter().writeValueAsString(out).getBytes(StandardCharsets.UTF_8))
    println(s"Wrote $outPath")
    spark.stop()
  }
}
