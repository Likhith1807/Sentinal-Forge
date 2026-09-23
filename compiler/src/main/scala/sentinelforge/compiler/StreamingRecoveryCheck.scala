package sentinelforge.compiler

import org.apache.spark.sql.SparkSession
import org.apache.spark.sql.streaming.Trigger
import org.apache.spark.sql.types._
import org.apache.spark.sql.functions._

/** Phase E item 17: kill and restart a streaming job, and show real checkpoint recovery — not a
  * description of the mechanism, an actual run of it.
  *
  * `StreamingCheck.scala` already proves `PolicyCompare` runs as a genuine incremental stream; this
  * proves that stream survives a restart without reprocessing or dropping input. Sequence:
  *
  *   1. Start query 1 (real checkpoint dir, durable JSON file sink — not "memory", which doesn't
  *      survive a restart), feed the first half of events, wait for them to be processed.
  *   2. `query.stop()` — a genuine stop of the running StreamingQuery, not a description of one.
  *   3. Write the SECOND half of events into the watched source dir while nothing is running,
  *      simulating input that arrived during the outage.
  *   4. Start query 2 — an entirely new StreamingQuery object, same checkpoint dir, same source,
  *      same sink. Structured Streaming's checkpoint tracks the last committed source offset, so
  *      query 2 must resume from exactly there: it must not re-emit the first half's alerts
  *      (checked directly, not assumed) and must correctly process the second half.
  *   5. Compare the sink's total accumulated output — across both queries — against the same
  *      events run through the batch path (`ReplayCheck`'s code path) once, uninterrupted. They
  *      must match exactly: recovery must be lossless, not just "didn't crash."
  */
object StreamingRecoveryCheck {

  private val eventSchema = StructType(Seq(
    StructField("event_id", StringType, nullable = false),
    StructField("timestamp", StringType, nullable = false),
    StructField("account_id", StringType, nullable = false),
    StructField("event_type", StringType, nullable = false),
    StructField("source_host", StringType, nullable = false),
    StructField("source_ip", StringType, nullable = true),
    StructField("auth_method", StringType, nullable = false),
    StructField("mfa_used", BooleanType, nullable = false),
    StructField("session_id", StringType, nullable = true),
  ))

  private def deleteRecursively(f: java.io.File): Unit = {
    if (f.exists()) {
      if (f.isDirectory) f.listFiles().foreach(deleteRecursively)
      f.delete()
    }
  }

  private def writeEventFile(spark: SparkSession, dir: java.io.File, row: org.apache.spark.sql.Row, name: String): Unit = {
    val tmp = new java.io.File(s"${dir.getAbsolutePath}/_tmp-$name")
    spark.createDataFrame(spark.sparkContext.parallelize(Seq(row)), eventSchema).coalesce(1).write.mode("append").json(tmp.getAbsolutePath)
    tmp.listFiles().filter(_.getName.startsWith("part-")).foreach(_.renameTo(new java.io.File(s"${dir.getAbsolutePath}/$name.json")))
    deleteRecursively(tmp)
  }

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("sentinel-forge-streaming-recovery-check").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")

    val repoRoot = new java.io.File(".").getCanonicalPath
    val base = new java.io.File(s"$repoRoot/target/streaming-recovery")
    deleteRecursively(base)
    val sourceDir = new java.io.File(s"${base.getAbsolutePath}/source")
    val checkpointDir = new java.io.File(s"${base.getAbsolutePath}/checkpoint")
    val sinkDir = new java.io.File(s"${base.getAbsolutePath}/sink")
    Seq(sourceDir, checkpointDir, sinkDir).foreach(_.mkdirs())

    val policy = spark.read.option("multiLine", "true").json(s"$repoRoot/data/samples/schema/account_policy_reference.json")
      .select(explode(col("records")).as("r")).select("r.*")
    val spec = CompiledSpec.load(spark, s"$repoRoot/data/samples/ir/compiled/mfa-missing-on-required-account.compiled.json")

    val allEvents = spark.read.json(s"$repoRoot/data/samples/replay/mfa_bypass_events.jsonl")
      .select(eventSchema.fieldNames.map(col): _*).collect()
    val (firstHalf, secondHalf) = allEvents.splitAt((allEvents.length + 1) / 2)
    println(s"Total events: ${allEvents.length} (first half: ${firstHalf.length}, second half: ${secondHalf.length})")

    def startQuery() = {
      val streamingEvents = spark.readStream.schema(eventSchema).option("maxFilesPerTrigger", 1).json(sourceDir.getAbsolutePath)
      val alerts = RuleCompiler.compile(spec, streamingEvents, Some(policy))
      alerts.writeStream
        .format("json")
        .option("path", sinkDir.getAbsolutePath)
        .option("checkpointLocation", checkpointDir.getAbsolutePath)
        .outputMode("append")
        .trigger(Trigger.ProcessingTime("1 second"))
        .start()
    }

    // --- Phase 1: query 1 processes the first half, then is genuinely stopped ---
    val query1 = startQuery()
    firstHalf.zipWithIndex.foreach { case (row, i) =>
      writeEventFile(spark, sourceDir, row, s"event-$i")
      Thread.sleep(1200)
    }
    query1.processAllAvailable()
    val afterFirstHalf = spark.read.schema(new StructType().add("groupKey", StringType).add("triggeringEventId", StringType).add("status", StringType))
      .json(sinkDir.getAbsolutePath).collect().map(_.getString(1)).toSet
    println(s"After first half, before stop: ${afterFirstHalf.size} alert rows written: $afterFirstHalf")
    val batchesBeforeStop = query1.recentProgress.count(_.numInputRows > 0)
    query1.stop() // a genuine stop — the StreamingQuery's background thread is torn down here.
    println(s"query1 stopped after $batchesBeforeStop non-empty micro-batches.")

    // --- Phase 2: "outage" — write the second half while NOTHING is running ---
    secondHalf.zipWithIndex.foreach { case (row, i) => writeEventFile(spark, sourceDir, row, s"event-late-$i") }
    println(s"Wrote ${secondHalf.length} more events to the source dir while no query was running.")

    // --- Phase 3: query 2 — a brand-new StreamingQuery, same checkpoint — must resume, not restart ---
    val query2 = startQuery()
    query2.processAllAvailable()
    val batchesAfterRestart = query2.recentProgress.count(_.numInputRows > 0)
    query2.stop()

    val finalRows = spark.read.schema(new StructType().add("groupKey", StringType).add("triggeringEventId", StringType).add("status", StringType))
      .json(sinkDir.getAbsolutePath).collect()
    val finalByEvent = finalRows.map(r => r.getString(1) -> r.getString(2)).toMap
    println(s"Final sink contents (${finalRows.length} rows): $finalByEvent")

    // --- Verification 1: no duplicate rows for any event (recovery didn't reprocess committed work) ---
    val duplicates = finalRows.map(_.getString(1)).groupBy(identity).collect { case (id, xs) if xs.length > 1 => id }
    val noDuplicates = duplicates.isEmpty

    // --- Verification 2: every event, from both halves, got exactly the batch-mode answer ---
    val batchEvents = spark.createDataFrame(spark.sparkContext.parallelize(allEvents.toSeq), eventSchema)
    val batchByEvent = RuleCompiler.compile(spec, batchEvents, Some(policy)).select("triggeringEventId", "status")
      .collect().map(r => r.getString(0) -> r.getString(1)).toMap
    val matchesBatch = finalByEvent == batchByEvent

    val recoveryWorked = noDuplicates && matchesBatch && batchesAfterRestart > 0
    println(s"\nno duplicate rows across restart: $noDuplicates")
    println(s"final streamed result matches uninterrupted batch result: $matchesBatch (batch=$batchByEvent)")
    println(s"query2 (post-restart) processed $batchesAfterRestart non-empty micro-batch(es) — proves it did real new work, not a no-op")
    println(s"\nRECOVERY CHECK: ${if (recoveryWorked) "PASS" else "FAIL"}")

    val outPath = s"$repoRoot/experiments/results/phaseE_streaming_recovery_check.json"
    val json =
      s"""{
         |  "totalEvents": ${allEvents.length},
         |  "firstHalfEvents": ${firstHalf.length},
         |  "secondHalfEvents": ${secondHalf.length},
         |  "microBatchesBeforeStop": $batchesBeforeStop,
         |  "microBatchesAfterRestart": $batchesAfterRestart,
         |  "noDuplicateRowsAfterRecovery": $noDuplicates,
         |  "recoveredResultMatchesUninterruptedBatch": $matchesBatch,
         |  "pass": $recoveryWorked
         |}
         |""".stripMargin
    val pw = new java.io.PrintWriter(outPath, "UTF-8")
    try pw.write(json) finally pw.close()
    println(s"Wrote $outPath")

    spark.stop()
    if (!recoveryWorked) System.exit(1)
  }
}
