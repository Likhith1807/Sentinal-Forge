package sentinelforge.compiler

import org.apache.spark.sql.{SparkSession}
import org.apache.spark.sql.streaming.Trigger
import org.apache.spark.sql.types._
import org.apache.spark.sql.functions._

/** Proves the SequenceThenTrigger... no — proves `PolicyCompare` runs
  * unmodified on a real Spark Structured Streaming query, not just batch.
  *
  * Why only PolicyCompare: Spark Structured Streaming does not support
  * non-time-based analytic window functions
  * (`Window.partitionBy(...).orderBy(...).rangeBetween(...)`) on a
  * streaming DataFrame — this is a real, documented Spark limitation, not
  * an implementation gap here. `SequenceThenTrigger` and
  * `DistinctCountWithinWindow` both use exactly that pattern (see
  * RuleCompiler.scala), so running them as true incremental streams needs
  * a genuinely different implementation — Structured Streaming's
  * time-window + watermark `groupBy` — which is real, scoped follow-up
  * work, not something silently skipped here. `PolicyCompare` uses no
  * window function at all (a stream-static join plus a filter), which
  * Structured Streaming supports natively — so this runs the *exact same*
  * `RuleCompiler.compile` call Stage 4's batch path uses, unmodified,
  * proving the compiler's output genuinely works on both paths rather than
  * only ever having been run in batch.
  */
object StreamingCheck {

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

  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("sentinel-forge-streaming-check").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    import spark.implicits._

    val repoRoot = new java.io.File(".").getCanonicalPath
    val streamDir = new java.io.File(s"$repoRoot/target/streaming-input")
    if (streamDir.exists()) streamDir.listFiles().foreach(_.delete()) else streamDir.mkdirs()

    val policy = spark.read.option("multiLine", "true").json(s"$repoRoot/data/samples/schema/account_policy_reference.json")
      .select(explode(col("records")).as("r")).select("r.*")

    val spec = CompiledSpec.load(spark, s"$repoRoot/data/samples/ir/compiled/mfa-missing-on-required-account.compiled.json")

    val streamingEvents = spark.readStream.schema(eventSchema).option("maxFilesPerTrigger", 1).json(streamDir.getAbsolutePath)

    // The exact same call ReplayCheck.scala makes in batch mode — no
    // streaming-specific branch in RuleCompiler at all.
    val alerts = RuleCompiler.compile(spec, streamingEvents, Some(policy))

    val query = alerts.writeStream
      .format("memory")
      .queryName("streaming_alerts")
      .outputMode("append")
      .trigger(Trigger.ProcessingTime("2 seconds"))
      .start()

    // Drip-feed the 3 real mfa_bypass replay events in as SEPARATE files,
    // with a real delay between each, while the query above is already
    // running — this is what makes it a genuine incremental stream rather
    // than one big batch read dressed up as streaming.
    // spark.read.json infers columns in (roughly alphabetical) order, which
    // won't match eventSchema's declared order — and createDataFrame(RDD[Row],
    // StructType) below is purely positional, not name-matched. Re-selecting
    // in eventSchema's exact column order avoids silently scrambling fields.
    val events = spark.read.json(s"$repoRoot/data/samples/replay/mfa_bypass_events.jsonl")
      .select(eventSchema.fieldNames.map(col): _*)
      .collect()
    println(s"Drip-feeding ${events.length} events into $streamDir, one every 3s, while the streaming query runs...")

    events.zipWithIndex.foreach { case (row, i) =>
      val singleEventDf = spark.createDataFrame(spark.sparkContext.parallelize(Seq(row)), eventSchema)
      singleEventDf.coalesce(1).write.mode("append").json(s"${streamDir.getAbsolutePath}/batch-$i")
      // Structured Streaming's file source wants files directly in the
      // watched dir, not subdirectories — move the part file up, drop Spark's _SUCCESS marker.
      val batchDir = new java.io.File(s"${streamDir.getAbsolutePath}/batch-$i")
      batchDir.listFiles().filter(_.getName.startsWith("part-")).foreach { f =>
        f.renameTo(new java.io.File(s"${streamDir.getAbsolutePath}/event-$i.json"))
      }
      def deleteRecursively(f: java.io.File): Unit = {
        if (f.isDirectory) f.listFiles().foreach(deleteRecursively)
        f.delete()
      }
      deleteRecursively(batchDir)
      println(s"  [t+${i * 3}s] wrote event-$i.json (account_id=${row.getAs[String]("account_id")})")
      Thread.sleep(3000)
    }

    Thread.sleep(4000) // let the final micro-batch flush
    query.processAllAvailable()

    println(s"\nStreaming query ran ${query.recentProgress.length} micro-batches (proof this was incremental, not one batch read).")
    println("Alerts accumulated in the memory sink:")
    spark.sql("select groupKey, triggeringEventId, status from streaming_alerts").show(truncate = false)

    val outPath = s"$repoRoot/experiments/results/phase4_streaming_check.json"
    val rows = spark.sql("select groupKey, triggeringEventId, status from streaming_alerts").collect()
    val json = rows.map(r => s"""{"groupKey":"${r.getString(0)}","triggeringEventId":"${r.getString(1)}","status":"${r.getString(2)}"}""")
      .mkString("[\n  ", ",\n  ", "\n]\n")
    val pw = new java.io.PrintWriter(outPath, "UTF-8")
    try pw.write(s"""{"microBatchCount": ${query.recentProgress.length}, "alerts": $json}""") finally pw.close()
    println(s"Wrote $outPath")

    query.stop()
    spark.stop()
  }
}
