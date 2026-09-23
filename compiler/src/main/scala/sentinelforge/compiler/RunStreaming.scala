package sentinelforge.compiler

import java.nio.file.{Files, Paths, StandardCopyOption}
import java.nio.charset.StandardCharsets

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.streaming.{StreamingQueryListener, StreamingQueryProgress}

/** Runs one compiled rule as a Structured Streaming query over a directory of JSON-lines files.
  *
  *   RunStreaming --spec rule.json --source dir --out dir --checkpoint dir [--policy policy.json]
  *                [--lateness-seconds 30] [--expiry-seconds 3600] [--trigger-seconds 1]
  *                [--available-now true|false] [--max-files-per-trigger 1000] [--idle-exit-seconds 0]
  *
  * Output (see StreamingEngine for the semantics): out/{alert,late,duplicate,quarantine,policy}/batch-<id>.jsonl,
  * out/progress/batch-<id>.json (Spark's own progress, incl. rows dropped by the watermark) and out/stream.json.
  *
  * A heartbeat event - event_type "__flush__", any valid timestamp, non-null key columns - advances the watermark
  * so idle keys are flushed; without later events or a heartbeat, the last `lateness` of each key's tail is held back.
  * Kill the process at any time and start it again with the same --checkpoint: it resumes where it committed.
  */
object RunStreaming {
  private def parse(args: Array[String]): Map[String, String] = args.sliding(2, 2).map { case Array(k, v) => k.stripPrefix("--") -> v }.toMap

  def main(args: Array[String]): Unit = {
    val o = parse(args)
    val out = Paths.get(o("out"))
    Files.createDirectories(out.resolve("progress"))
    val spark = SparkSession.builder().appName("sentinel-forge-streaming").master(o.getOrElse("master", "local[*]"))
      .config("spark.sql.session.timeZone", "UTC").config("spark.ui.enabled", "false")
      .config("spark.sql.shuffle.partitions", o.getOrElse("shuffle-partitions", "4")).getOrCreate()
    spark.sparkContext.setLogLevel("ERROR")
    val spec = CompiledSpec.load(spark, o("spec"))
    val cfg = StreamingEngine.Config(o.getOrElse("lateness-seconds", "30").toLong * 1000000L, o.getOrElse("expiry-seconds", "3600").toLong * 1000000L)
    require(cfg.expiry >= cfg.lateness, "expiry must be at least the lateness allowance")
    spec.timeWindowSeconds.foreach(w => require(cfg.expiry >= (w * 1000000L + cfg.lateness),
      s"expiry must be >= window + lateness ($w s + ${cfg.lateness / 1000000L} s) or state expiry could change alerts"))

    spark.streams.addListener(new StreamingQueryListener {
      override def onQueryStarted(e: StreamingQueryListener.QueryStartedEvent): Unit = ()
      override def onQueryTerminated(e: StreamingQueryListener.QueryTerminatedEvent): Unit = ()
      override def onQueryProgress(e: StreamingQueryListener.QueryProgressEvent): Unit = {
        val p: StreamingQueryProgress = e.progress
        val f = out.resolve("progress").resolve(f"batch-${p.batchId}%08d.json")
        val tmp = f.resolveSibling(f.getFileName.toString + ".tmp")
        Files.write(tmp, p.json.getBytes(StandardCharsets.UTF_8))
        Files.move(tmp, f, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
      }
    })

    val availableNow = o.getOrElse("available-now", "false").toBoolean
    val started = System.currentTimeMillis()
    val q = StreamingEngine.start(spark, spec, o("source"), o.get("policy"), out, o("checkpoint"), cfg,
      o.getOrElse("trigger-seconds", "1").toInt, availableNow, o.getOrElse("max-files-per-trigger", "1000").toInt)
    val idleExit = o.getOrElse("idle-exit-seconds", "0").toInt
    if (availableNow || idleExit == 0) q.awaitTermination()
    else {
      var idleSince = System.currentTimeMillis()
      while (q.isActive && System.currentTimeMillis() - idleSince < idleExit * 1000L) {
        Thread.sleep(300)
        val lp = q.lastProgress
        if (lp != null && lp.numInputRows > 0) idleSince = System.currentTimeMillis()
      }
      q.stop()
    }
    val progress = q.recentProgress
    val dropped = progress.flatMap(_.stateOperators.map(_.numRowsDroppedByWatermark)).sum
    val summary = s"""{"behaviourId":"${spec.behaviourId}","ruleHash":${spec.ruleHash.map("\"" + _ + "\"").getOrElse("null")},"latenessSeconds":${cfg.lateness / 1000000L},"expirySeconds":${cfg.expiry / 1000000L},"batchesInWindow":${progress.length},"rowsDroppedByWatermark":$dropped,"elapsedSeconds":${(System.currentTimeMillis() - started) / 1000.0}}"""
    Files.write(out.resolve("stream.json"), summary.getBytes(StandardCharsets.UTF_8))
    spark.stop()
  }
}
