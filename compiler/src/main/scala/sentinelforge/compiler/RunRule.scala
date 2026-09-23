package sentinelforge.compiler

import java.nio.charset.StandardCharsets
import java.nio.file.{Files, Path, Paths, StandardCopyOption}
import java.time.Instant

import scala.jdk.CollectionConverters._

import com.fasterxml.jackson.databind.ObjectMapper
import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

/** Executes ONE compiled rule against ONE dataset and writes a self-describing run directory:
  *
  *   alerts.jsonl      one JSON object per alert / insufficient_context result, with supporting-event evidence
  *   quarantine.jsonl  a capped sample of rows the rule could not evaluate, each with a reason
  *   run.json          rule hash, compiler + engine versions, input paths and schema, counts, timings, status
  *
  * This is the process the dashboard's job runner launches. Every file is written to a temp name and
  * atomically renamed, so a reader never observes a half-written result; on failure `run.json` is still
  * written (status "failed", with the error) and the directory is kept for diagnosis, never deleted.
  *
  * Usage:
  *   RunRule --spec rule.json --events data/events.jsonl [--policy policy.json] --out runs/<uuid>
  *           [--evidence true] [--dedupe true] [--stats true] [--master local[*]] [--max-alerts 50000]
  *
  * Exit codes: 0 completed, 2 bad arguments/spec, 3 dataset cannot evaluate the rule (schema), 1 other failure.
  */
object RunRule {

  private val mapper = new ObjectMapper()

  private def parseArgs(args: Array[String]): Map[String, String] = {
    args.sliding(2, 2).map {
      case Array(k, v) if k.startsWith("--") => k.drop(2) -> v
      case other => throw new IllegalArgumentException(s"malformed arguments near ${other.mkString(" ")}")
    }.toMap
  }

  private def atomicWrite(target: Path, content: String): Unit = {
    Files.createDirectories(target.getParent)
    val tmp = target.resolveSibling(target.getFileName.toString + ".tmp")
    Files.write(tmp, content.getBytes(StandardCharsets.UTF_8))
    Files.move(tmp, target, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE)
  }

  private def readEvents(spark: SparkSession, path: String, format: String): DataFrame = {
    val fmt = if (format != "auto") format else if (path.toLowerCase.endsWith(".jsonl") || path.toLowerCase.endsWith(".json")) "json" else "parquet"
    fmt match {
      case "json"    => spark.read.json(path)
      case "parquet" => spark.read.parquet(path)
      case other     => throw new IllegalArgumentException(s"unsupported events format '$other'")
    }
  }

  /** Accepts the documented `{"records":[...]}` policy document, a JSONL file of records, or Parquet. */
  private def readPolicy(spark: SparkSession, path: String): DataFrame = {
    val lower = path.toLowerCase
    if (lower.endsWith(".parquet") || Files.isDirectory(Paths.get(path))) spark.read.parquet(path)
    else if (lower.endsWith(".jsonl")) spark.read.json(path)
    else {
      val doc = spark.read.option("multiLine", "true").json(path)
      if (doc.columns.contains("records")) doc.select(explode(col("records")).as("r")).select("r.*") else doc
    }
  }

  private def schemaOf(df: DataFrame): java.util.Map[String, String] = {
    val m = new java.util.LinkedHashMap[String, String]()
    df.schema.fields.foreach(f => m.put(f.name, f.dataType.simpleString))
    m
  }

  def main(args: Array[String]): Unit = {
    val startedAt = Instant.now().toString
    val t0 = System.nanoTime()
    val opts = try parseArgs(args) catch { case e: IllegalArgumentException => System.err.println(e.getMessage); sys.exit(2) }
    val outDir = Paths.get(opts.getOrElse("out", { System.err.println("--out is required"); sys.exit(2) }))
    val run = new java.util.LinkedHashMap[String, AnyRef]()
    run.put("runId", opts.getOrElse("run-id", outDir.getFileName.toString))
    run.put("engine", "spark-batch")
    run.put("startedAt", startedAt)
    run.put("javaVersion", System.getProperty("java.version"))

    def finish(status: String, code: Int, extra: Map[String, AnyRef] = Map.empty): Nothing = {
      run.put("status", status)
      run.put("finishedAt", Instant.now().toString)
      run.put("elapsedSeconds", java.lang.Double.valueOf((System.nanoTime() - t0) / 1e9))
      extra.foreach { case (k, v) => run.put(k, v) }
      atomicWrite(outDir.resolve("run.json"), mapper.writerWithDefaultPrettyPrinter().writeValueAsString(run))
      sys.exit(code)
    }

    var spark: SparkSession = null
    try {
      val specPath   = opts.getOrElse("spec", { System.err.println("--spec is required"); sys.exit(2) })
      val eventsPath = opts.getOrElse("events", { System.err.println("--events is required"); sys.exit(2) })
      run.put("input", Map("events" -> eventsPath, "policy" -> opts.getOrElse("policy", null), "spec" -> specPath).asJava)

      spark = SparkSession.builder().appName("sentinel-forge-run").master(opts.getOrElse("master", "local[*]"))
        .config("spark.sql.session.timeZone", "UTC").config("spark.ui.enabled", "false")
        .config("spark.sql.shuffle.partitions", opts.getOrElse("shuffle-partitions", "16")).getOrCreate()
      spark.sparkContext.setLogLevel("ERROR")
      run.put("sparkVersion", spark.version)

      val spec = CompiledSpec.load(spark, specPath)
      run.put("behaviourId", spec.behaviourId)
      run.put("ruleHash", spec.ruleHash.orNull)
      run.put("compilerVersion", spec.compilerVersion.orNull)

      val tLoad = System.nanoTime()
      val events = readEvents(spark, eventsPath, opts.getOrElse("events-format", "auto"))
      run.put("eventSchema", schemaOf(events))
      val policy = opts.get("policy").map(readPolicy(spark, _))
      policy.foreach(p => run.put("policySchema", schemaOf(p)))
      if (spec.recipe == "PolicyCompare" && policy.isEmpty) {
        finish("failed", 2, Map("error" -> Map("kind" -> "missing_policy", "message" -> "this rule joins a policy reference; --policy is required").asJava))
      }

      RuleCompiler.requireColumns(spec, events)             // structured failure before any work is done
      policy.foreach { p =>
        val need = spec.policyField.toSeq :+ "account_id"
        val missing = need.filterNot(p.columns.contains)
        if (missing.nonEmpty) throw new RuleCompiler.SchemaMismatchException(missing.map(c => RuleCompiler.SchemaProblem(s"policy.$c", "missing")))
      }

      val dedupe = opts.getOrElse("dedupe", "true").toBoolean
      val (clean, quarantine) = RuleCompiler.prepare(spec, events, dedupe)
      val cleanCached = clean.cache()
      val alertsAll = RuleCompiler.compilePrepared(spec, cleanCached, policy)
      val withEvidence = if (opts.getOrElse("evidence", "true").toBoolean) RuleCompiler.attachEvidence(spec, alertsAll, cleanCached) else alertsAll
      val reportable = withEvidence.filter(col("status") =!= "no_alert").orderBy("detectedAtMicros", "triggeringEventId")
      val maxAlerts = opts.getOrElse("max-alerts", "50000").toInt
      val rows = reportable.limit(maxAlerts + 1).toJSON.collect()
      val tExec = System.nanoTime()

      val truncated = rows.length > maxAlerts
      val shown = if (truncated) rows.take(maxAlerts) else rows
      atomicWrite(outDir.resolve("alerts.jsonl"), shown.mkString("", "\n", if (shown.isEmpty) "" else "\n"))

      val counts = new java.util.LinkedHashMap[String, AnyRef]()
      if (opts.getOrElse("stats", "true").toBoolean) {
        val raw = events.count()
        val quarantined = quarantine.count()
        val cleanN = cleanCached.count()
        counts.put("eventsRead", java.lang.Long.valueOf(raw))
        counts.put("eventsEvaluated", java.lang.Long.valueOf(cleanN))
        counts.put("quarantined", java.lang.Long.valueOf(quarantined))
        counts.put("duplicatesDropped", java.lang.Long.valueOf(raw - quarantined - cleanN))
        val byReason = quarantine.groupBy("reason").count().collect().map(r => r.getString(0) -> java.lang.Long.valueOf(r.getLong(1))).toMap
        counts.put("quarantineByReason", byReason.asJava)
        val byStatus = alertsAll.groupBy("status").count().collect().map(r => r.getString(0) -> java.lang.Long.valueOf(r.getLong(1))).toMap
        counts.put("resultsByStatus", byStatus.asJava)
        val qs = quarantine.limit(1000).toJSON.collect()
        atomicWrite(outDir.resolve("quarantine.jsonl"), qs.mkString("", "\n", if (qs.isEmpty) "" else "\n"))
      }
      counts.put("alertsWritten", java.lang.Integer.valueOf(shown.length))
      counts.put("alertsTruncated", java.lang.Boolean.valueOf(truncated))
      run.put("counts", counts)
      run.put("timings", Map(
        "loadSeconds" -> java.lang.Double.valueOf((tLoad - t0) / 1e9),
        "executeSeconds" -> java.lang.Double.valueOf((tExec - tLoad) / 1e9)).asJava)
      run.put("options", Map("dedupe" -> java.lang.Boolean.valueOf(dedupe), "evidence" -> opts.getOrElse("evidence", "true")).asJava)
      finish("completed", 0)
    } catch {
      case e: RuleCompiler.SchemaMismatchException =>
        val problems = e.problems.map(p => Map("column" -> p.column, "problem" -> p.problem).asJava).asJava
        finish("failed", 3, Map("error" -> Map("kind" -> "schema_mismatch", "message" -> e.getMessage, "problems" -> problems).asJava))
      case e: Throwable =>
        finish("failed", 1, Map("error" -> Map("kind" -> e.getClass.getSimpleName, "message" -> String.valueOf(e.getMessage)).asJava))
    } finally {
      if (spark != null) spark.stop()
    }
  }
}
