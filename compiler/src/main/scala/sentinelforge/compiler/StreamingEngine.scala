package sentinelforge.compiler

import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Path, Paths, StandardCopyOption}
import java.time.{Instant, ZoneOffset}
import java.time.format.DateTimeFormatter

import scala.collection.mutable
import scala.collection.mutable.ArrayBuffer

import org.apache.spark.sql.{DataFrame, Dataset, Encoders, SparkSession}
import org.apache.spark.sql.functions._
import org.apache.spark.sql.streaming.{GroupState, GroupStateTimeout, OutputMode, StreamingQueryListener, Trigger}
import org.apache.spark.sql.types._

// Stateful-operator row/state types. Top level (not nested) so Spark's reflection-based encoders resolve them.
case class SEv(event_id: String, ts: Long, tsText: String, key: String, value: String, etype: String, valid: Boolean, reason: String,
               event_ts: java.sql.Timestamp) // carried through so the watermark attribute stays visible to the stateful operator
case class SBuf(id: String, ts: Long, tsText: String, value: String, etype: String)
case class SKeyState(maxSeen: Long, lastFinal: Long, prevBreaching: Boolean, buffer: Seq[SBuf], history: Seq[SBuf])
case class SEvidence(tsMicros: Long, eventId: String, timestamp: String, value: String)
case class SOut(kind: String, groupKey: String, triggeringEventId: String, detectedAt: String, detectedAtMicros: Long,
                windowStart: String, matchedCount: Long, evidence: Seq[SEvidence], reason: String)

/** Structured Streaming execution of all five behaviours, with the guarantees written down instead of implied.
  *
  * WINDOWED RECIPES (SequenceThenTrigger, DistinctCountWithinWindow) run in one stateful operator per key:
  *  - Events are buffered and FINALISED in (timestamp, event_id) order once they are at least `lateness` older than
  *    the newest event the key has seen. Evaluating in timestamp order is what makes the streaming result equal the
  *    batch result: the batch engine sorts the whole input, the stream reconstructs the same order.
  *  - LATENESS POLICY: an event whose timestamp is at or before the key's last finalised timestamp can no longer be
  *    placed in order. It is not silently dropped: a `late` record (id, timestamp, reason) is emitted. Batch/stream
  *    agreement is therefore defined against the input MINUS the events reported late.
  *  - DUPLICATES: an event_id already buffered or inside the retained window is emitted as a `duplicate` record and
  *    ignored. A redelivery older than the retained window surfaces as `late`. Beyond state expiry it is undetectable.
  *  - STATE EXPIRY: a key's state is dropped when the global event-time watermark (max event time - expiry) passes
  *    the key's newest event + lateness, i.e. once global time is `expiry + lateness` past the key's last event, after
  *    flushing its buffer. Because expiry >= window + lateness, an expired key
  *    holds nothing a later event could need, so expiry cannot change any alert (a too-small expiry can - the limit
  *    is documented and tested). Rows older than the global watermark are dropped by Spark itself and only COUNTED
  *    (query progress `numRowsDroppedByWatermark`), not identified.
  *  - LATENCY: an alert appears once its timestamp is `lateness` behind the key's newest event (or after a heartbeat /
  *    expiry flush). That delay is the price of exact agreement with batch; set lateness=0 for in-order sources.
  *  - QUARANTINE: malformed timestamps / null keys become `quarantine` records, same reasons as the batch engine.
  *
  * POLICY RECIPES are stateless per event. The policy table is RE-READ for every micro-batch, and versioned rows
  * (`effective_from`) are applied at each event's own timestamp; every result records the `policyVersion` it used. A
  * policy update is never retroactive to results already emitted.
  *
  * OUTPUT is written from `foreachBatch` to one file per kind per batch id, atomically and idempotently: replaying a
  * batch after a crash overwrites the same file, so a restart cannot duplicate output. That is "effectively once" under
  * stated conditions (replayable, immutable source files; deterministic state function; single writer per checkpoint),
  * not an unconditional exactly-once claim.
  */
object StreamingEngine {

  final case class Config(lateness: Long, expiry: Long) // microseconds

  private val Fmt = DateTimeFormatter.ofPattern("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'").withZone(ZoneOffset.UTC)
  private def render(micros: Long): String = Fmt.format(Instant.ofEpochSecond(Math.floorDiv(micros, 1000000L), Math.floorMod(micros, 1000000L) * 1000L))

  val EventSchema: StructType = StructType(Seq(
    StructField("event_id", StringType), StructField("timestamp", StringType), StructField("account_id", StringType),
    StructField("event_type", StringType), StructField("source_host", StringType), StructField("source_ip", StringType),
    StructField("auth_method", StringType), StructField("mfa_used", BooleanType), StructField("session_id", StringType)))

  // --- per-key stateful function (windowed recipes) ---------------------------------------------------------------
  private def finalizeGroup(spec: CompiledSpec, st: SKeyState, group: Seq[SBuf], t: Long, out: ArrayBuffer[SOut], key: String): SKeyState = {
    val w = spec.timeWindowSeconds.get * 1000000L
    val kept = st.history.filter(_.ts >= t - w)
    spec.recipe match {
      case "SequenceThenTrigger" =>
        val history = kept ++ group.filter(_.etype == spec.countEventType.get)
        val need = spec.countThreshold.get
        group.filter(_.etype == spec.triggerEventType.get).sortBy(_.id).foreach { s =>
          if (history.length >= need)
            out += SOut("alert", key, s.id, s.tsText, s.ts, render(s.ts - w), history.length, history.sortBy(h => (h.ts, h.id)).map(h => SEvidence(h.ts, h.id, h.tsText, h.etype)), null)
        }
        st.copy(lastFinal = t, history = history)
      case _ =>
        val history = kept ++ group
        val distinct = history.map(_.value).toSet.size
        val breaching = distinct >= spec.distinctThreshold.get
        if (breaching && !st.prevBreaching) {
          val first = group.minBy(_.id)
          out += SOut("alert", key, first.id, first.tsText, first.ts, render(first.ts - w), distinct,
            history.sortBy(h => (h.ts, h.id)).map(h => SEvidence(h.ts, h.id, h.tsText, h.value)), null)
        }
        st.copy(lastFinal = t, history = history, prevBreaching = breaching)
    }
  }

  private def finalizeUpTo(spec: CompiledSpec, st0: SKeyState, threshold: Long, out: ArrayBuffer[SOut], key: String): SKeyState = {
    var st = st0
    var buf = st.buffer.sortBy(b => (b.ts, b.id))
    while (buf.nonEmpty && buf.head.ts <= threshold) {
      val t = buf.head.ts
      val (group, rest) = buf.span(_.ts == t)
      st = finalizeGroup(spec, st.copy(buffer = rest), group, t, out, key)
      buf = rest
    }
    st.copy(buffer = buf)
  }

  private def process(spec: CompiledSpec, cfg: Config)(key: String, rows: Iterator[SEv], gs: GroupState[SKeyState]): Iterator[SOut] = {
    val out = ArrayBuffer[SOut]()
    if (key == "__flush__") return Iterator.empty
    if (key == "__quarantine__") {
      rows.foreach(e => out += SOut("quarantine", null, e.event_id, e.tsText, 0L, null, 0L, Nil, e.reason))
      return out.iterator
    }
    var st = if (gs.exists) gs.get else SKeyState(Long.MinValue, Long.MinValue, prevBreaching = false, Nil, Nil)
    if (gs.hasTimedOut) {
      st = finalizeUpTo(spec, st, Long.MaxValue, out, key)
      gs.remove()
      return out.iterator
    }
    val known = mutable.Set[String]() ++= st.buffer.map(_.id) ++= st.history.map(_.id)
    val buffer = ArrayBuffer[SBuf]() ++= st.buffer
    var maxSeen = st.maxSeen
    rows.toSeq.sortBy(e => (e.ts, e.event_id)).foreach { e =>
      if (known.contains(e.event_id)) out += SOut("duplicate", key, e.event_id, e.tsText, e.ts, null, 0L, Nil, "event_id already seen inside the retained window")
      else if (e.ts <= st.lastFinal) out += SOut("late", key, e.event_id, e.tsText, e.ts, null, 0L, Nil, "older than the key's finalised time; cannot be placed in order")
      else { buffer += SBuf(e.event_id, e.ts, e.tsText, e.value, e.etype); known += e.event_id; maxSeen = math.max(maxSeen, e.ts) }
    }
    st = finalizeUpTo(spec, st.copy(maxSeen = maxSeen, buffer = buffer.toSeq), maxSeen - cfg.lateness, out, key)
    gs.update(st)
    val wm = gs.getCurrentWatermarkMs()
    // The tail is flushed (and the key's state dropped) once global event time has moved `expiry + lateness` past the key's
    // newest event: the watermark is max event time - expiry, and the timeout is newest + lateness.
    gs.setTimeoutTimestamp(math.max(maxSeen / 1000L + cfg.lateness / 1000L, wm + 1L))
    out.iterator
  }

  // --- pipeline ----------------------------------------------------------------------------------------------------
  /** Adds ts_micros / valid / reason exactly as RuleCompiler.prepare decides them, without the batch-only dedupe. */
  private def parsed(spec: CompiledSpec, events: DataFrame): DataFrame = {
    val keyCols: Seq[String] = spec.recipe match {
      case "SequenceThenTrigger"       => Seq(spec.groupingKey.get)
      case "DistinctCountWithinWindow" => Seq(spec.groupingKey.get, spec.distinctField.get)
      case _                           => Seq("account_id")
    }
    val withTs = events.withColumn("ts_micros", RuleCompiler.parseMicros(col("timestamp").cast("string")))
    val reason = keyCols.foldLeft(
      when(col("event_id").isNull, lit("null_event_id")).when(col("timestamp").isNull, lit("null_timestamp"))
        .when(col("ts_micros").isNull, lit("malformed_timestamp")))((acc, k) => acc.when(col(k).isNull, lit(s"null_$k")))
    withTs.withColumn("_reason", reason).withColumn("_valid", col("_reason").isNull)
      .withColumn("event_ts", (col("ts_micros").cast("decimal(30,0)") / lit(1000000).cast("decimal(30,0)")).cast("timestamp"))
  }

  private def atomicWrite(path: Path, content: String): Unit = {
    Files.createDirectories(path.getParent)
    val tmp = path.resolveSibling(path.getFileName.toString + ".tmp")
    Files.write(tmp, content.getBytes(StandardCharsets.UTF_8))
    Files.move(tmp, path, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
  }

  private def writeKinds(outDir: Path, batchId: Long, jsonRows: Array[(String, String)]): Unit = {
    val emitted = System.currentTimeMillis()
    val byKind = jsonRows.groupBy(_._1)
    Seq("alert", "late", "duplicate", "quarantine", "policy").foreach { kind =>
      val lines = byKind.getOrElse(kind, Array.empty).map(_._2)
      // one file per kind per batch id, written even when empty so "batch N was processed" is observable
      atomicWrite(outDir.resolve(kind).resolve(f"batch-$batchId%08d.jsonl"), lines.map(l => l.stripSuffix("}") + s""","emittedAtMs":$emitted}""").mkString("", "\n", if (lines.isEmpty) "" else "\n"))
    }
  }

  /** Starts the query for `spec` reading JSON files from `sourceDir`. Returns the StreamingQuery. */
  def start(spark: SparkSession, spec: CompiledSpec, sourceDir: String, policyPath: Option[String], outDir: Path,
            checkpoint: String, cfg: Config, triggerSeconds: Int, availableNow: Boolean, maxFilesPerTrigger: Int) = {
    import spark.implicits._
    val raw = spark.readStream.schema(EventSchema).option("maxFilesPerTrigger", maxFilesPerTrigger.toString).json(sourceDir)
    RuleCompiler.requireColumns(spec, raw)
    val p = parsed(spec, raw)
    val watermarked = p.withWatermark("event_ts", s"${cfg.expiry / 1000000L} seconds")
    val trigger = if (availableNow) Trigger.AvailableNow() else Trigger.ProcessingTime(s"$triggerSeconds seconds")

    if (spec.recipe == "PolicyCompare") {
      val deduped = watermarked.dropDuplicatesWithinWatermark("event_id")
      deduped.writeStream.option("checkpointLocation", checkpoint).trigger(trigger).foreachBatch { (batch: DataFrame, batchId: Long) =>
        val bad = batch.filter(!col("_valid")).select(lit("quarantine").as("kind"), to_json(struct(
          lit("quarantine").as("kind"), col("event_id").as("triggeringEventId"), col("timestamp").as("detectedAt"), col("_reason").as("reason"))).as("json"))
        val clean = batch.filter(col("_valid")).drop("_reason", "_valid", "event_ts")
        val policy = policyPath.map { pp =>
          val doc = spark.read.option("multiLine", "true").json(pp)
          if (doc.columns.contains("records")) doc.select(explode(col("records")).as("r")).select("r.*") else doc
        }.getOrElse(throw new IllegalArgumentException("PolicyCompare needs --policy"))
        val results = RuleCompiler.compilePrepared(spec, clean, Some(policy)).filter(col("status") =!= "no_alert")
          .select(lit("policy").as("kind"), to_json(struct(results0(spec): _*)).as("json"))
        val rows = bad.unionByName(results).collect().map(r => (r.getString(0), r.getString(1)))
        // a policy result is an alert or an insufficient_context; both go to the `policy` kind, quarantine to its own
        writeKinds(outDir, batchId, rows)
      }.start()
    } else {
      val ev = watermarked.select(
        col("event_id"), coalesce(col("ts_micros"), lit(0L)).as("ts"), col("timestamp").as("tsText"),
        when(!col("_valid"), lit("__quarantine__")).when(col("event_type") === "__flush__", lit("__flush__")).otherwise(col(spec.groupingKey.get)).as("key"),
        (if (spec.recipe == "SequenceThenTrigger") col("event_type") else col(spec.distinctField.get)).as("value"),
        col("event_type").as("etype"), col("_valid").as("valid"), col("_reason").as("reason"), col("event_ts"))
      val relevantTypes: Seq[String] = spec.recipe match {
        case "SequenceThenTrigger" => Seq(spec.countEventType.get, spec.triggerEventType.get)
        case _                     => Seq(spec.filterEventType.get)
      }
      // heartbeat / irrelevant rows have already advanced the watermark above; they need no state
      // The heartbeat must stay a real row: a filter the optimizer pushes below the watermark node would hide it from the
      // watermark. It is routed to a no-op key instead of being filtered out.
      val relevant = ev.filter(!col("valid") || col("etype").isin((relevantTypes :+ "__flush__"): _*)).as[SEv]
      val out: Dataset[SOut] = relevant.groupByKey(_.key)
        .flatMapGroupsWithState[SKeyState, SOut](OutputMode.Append, GroupStateTimeout.EventTimeTimeout)(process(spec, cfg))
      out.writeStream.option("checkpointLocation", checkpoint).trigger(trigger).foreachBatch { (batch: Dataset[SOut], batchId: Long) =>
        val rows = batch.toDF().select(col("kind"), to_json(struct(batch.toDF().columns.map(col): _*)).as("json")).collect().map(r => (r.getString(0), r.getString(1)))
        writeKinds(outDir, batchId, rows)
      }.start()
    }
  }

  private def results0(spec: CompiledSpec) = Seq(
    col("groupKey"), col("triggeringEventId"), col("detectedAt"), col("detectedAtMicros"), col("observedValue"), col("expectedValue"),
    col("policyVersion"), col("status"), col("reason"), col("behaviourId"))
}
