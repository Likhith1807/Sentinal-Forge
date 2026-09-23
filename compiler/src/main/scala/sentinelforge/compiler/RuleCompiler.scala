package sentinelforge.compiler

import org.apache.spark.sql.{Column, DataFrame}
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions._

/** Stage 4: compiles a validated CompiledSpec into a Spark query and runs
  * it. A closed match over exactly the 3 recipes in
  * docs/spec/compiled-spec-format.md — not a general expression evaluator.
  * An unrecognized recipe fails loudly, the same "reject rather than
  * guess" discipline Stage 3 already applies to fields (see
  * compiler/src/observability_checker.py).
  *
  * Event-processing semantics (docs/spec/detection-semantics.md, "v2"):
  *  - Timestamps are parsed STRICTLY (RFC 3339, explicit `Z` or numeric offset, up to microsecond
  *    digits) and kept at microsecond precision. The earlier version truncated to whole seconds, which
  *    made 00:00:00.999 and 00:00:01.000 "the same instant" for window arithmetic.
  *  - A row whose timestamp is missing, malformed or ambiguous (no zone) is QUARANTINED and counted, never
  *    silently sorted to the front as null and never silently dropped.
  *  - Rows sharing an `event_id` collapse to exactly one (earliest timestamp, ties broken by a row hash), so
  *    a collector that redelivers an event cannot inflate a count or duplicate an alert.
  *  - Duplicate policy rows for one account collapse when identical; when they CONFLICT the account is
  *    `insufficient_context` (reason `policy_conflict`) rather than being joined once per row.
  */
object RuleCompiler {

  /** Output timestamp rendering; matches the input schema's `yyyy-MM-ddTHH:mm:ss.SSSZ` (UTC). */
  val TimestampFormat = "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"

  /** RFC 3339 with a mandatory zone, 0-6 fractional digits. Anything else is malformed by definition. */
  val TimestampPattern = "^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}:\\d{2}(\\.\\d{1,6})?(Z|[+-]\\d{2}:\\d{2})$"

  case class SchemaProblem(column: String, problem: String)
  class SchemaMismatchException(val problems: Seq[SchemaProblem])
    extends IllegalArgumentException("event data cannot evaluate this rule: " + problems.map(p => s"${p.column}: ${p.problem}").mkString("; "))

  /** Microseconds since the epoch as a Long, or null when the string is not a valid RFC 3339 instant. */
  private[compiler] def parseMicros(c: Column): Column =
    when(c.rlike(TimestampPattern), (c.cast("timestamp").cast("decimal(20,6)") * lit(1000000)).cast("long"))

  /** Renders epoch microseconds as `yyyy-MM-ddTHH:mm:ss.SSSZ` in UTC regardless of the session time zone:
    * date_format prints in the session zone, so the instant is first shifted by that zone's offset. */
  private def renderMicros(micros: Column): Column =
    date_format(to_utc_timestamp((micros.cast("decimal(30,0)") / lit(1000000).cast("decimal(30,0)")).cast("timestamp"), current_timezone()), TimestampFormat)

  /** (column, expected kind) pairs the rule reads or emits on the EVENT side; mirrors sentinelforge.behaviours. */
  def requiredEventColumns(spec: CompiledSpec): Seq[(String, String)] = {
    val base = Seq("event_id" -> "string", "timestamp" -> "string", "event_type" -> "string")
    val rest = spec.recipe match {
      case "SequenceThenTrigger"       => Seq(spec.groupingKey.get -> "string")
      case "DistinctCountWithinWindow" => Seq(spec.groupingKey.get -> "string", spec.distinctField.get -> "string")
      case "PolicyCompare" =>
        Seq("account_id" -> "string", spec.logField.get -> (if (spec.logField.get == "mfa_used") "boolean" else "string"))
      case other => throw new IllegalArgumentException(s"${spec.behaviourId}: unknown recipe '$other'")
    }
    (base ++ rest).distinct
  }

  private def kindOf(dt: org.apache.spark.sql.types.DataType): String = dt.typeName match {
    case "string" | "boolean" => dt.typeName
    case "timestamp" | "timestamp_ntz" => "string" // an already-parsed timestamp column is acceptable input
    case other => other
  }

  /** Fails with a structured SchemaMismatchException naming every missing or mistyped column. A column that is
    * entirely NULL (Spark type `null`) is present-but-unobserved: its rows degrade to insufficient_context rather
    * than failing the whole run. */
  def requireColumns(spec: CompiledSpec, events: DataFrame): Unit = {
    val have = events.schema.fields.map(f => f.name -> f.dataType).toMap
    val problems = requiredEventColumns(spec).flatMap { case (name, want) =>
      have.get(name) match {
        case None => Some(SchemaProblem(name, "missing"))
        case Some(dt) if kindOf(dt) != want && kindOf(dt) != "void" && kindOf(dt) != "null" => Some(SchemaProblem(name, s"expected $want but the data has ${dt.simpleString}"))
        case _ => None
      }
    }
    if (problems.nonEmpty) throw new SchemaMismatchException(problems)
  }

  /** Splits events into rows the rule can evaluate (with a `ts_micros` column, one row per event_id) and a
    * quarantine of rows it cannot, each with a machine-readable reason. */
  def prepare(spec: CompiledSpec, events: DataFrame, dedupe: Boolean = true): (DataFrame, DataFrame) = {
    requireColumns(spec, events)
    val keyCols: Seq[String] = spec.recipe match {
      case "SequenceThenTrigger"       => Seq(spec.groupingKey.get)
      case "DistinctCountWithinWindow" => Seq(spec.groupingKey.get, spec.distinctField.get)
      case _                           => Seq("account_id")
    }
    val withTs = events.withColumn("ts_micros", parseMicros(col("timestamp").cast("string")))
    val reason: Column = keyCols.foldLeft(
      when(col("event_id").isNull, lit("null_event_id"))
        .when(col("timestamp").isNull, lit("null_timestamp"))
        .when(col("ts_micros").isNull, lit("malformed_timestamp"))
    )((acc, k) => acc.when(col(k).isNull, lit(s"null_$k")))
    val tagged = withTs.withColumn("_quarantine_reason", reason)
    val quarantine = tagged.filter(col("_quarantine_reason").isNotNull)
      .select(col("event_id"), col("timestamp").cast("string").as("raw_timestamp"), col("_quarantine_reason").as("reason"))
    val ok = tagged.filter(col("_quarantine_reason").isNull).drop("_quarantine_reason")
    val clean =
      if (!dedupe || events.isStreaming) ok
      else {
        // Deterministic choice among rows sharing an event_id: earliest instant, then the lexicographically smallest
        // row content (all other columns, sorted by name, NULLs skipped). Written down in detection-semantics.md so the
        // independent reference engine makes the identical choice.
        val contentKey = concat_ws("\u0001", ok.columns.filterNot(_ == "ts_micros").sorted.map(c => col(c).cast("string")): _*)
        val w = Window.partitionBy("event_id").orderBy(col("ts_micros"), contentKey)
        ok.withColumn("_rn", row_number().over(w)).filter(col("_rn") === 1).drop("_rn")
      }
    (clean, quarantine)
  }

  def compile(spec: CompiledSpec, events: DataFrame, policy: Option[DataFrame] = None): DataFrame = {
    val (clean, _) = prepare(spec, events)
    compilePrepared(spec, clean, policy)
  }

  /** As `compile`, plus an `evidence` array on windowed alerts: the exact supporting events in the window. */
  def compileWithEvidence(spec: CompiledSpec, events: DataFrame, policy: Option[DataFrame] = None): DataFrame = {
    val (clean, _) = prepare(spec, events)
    val alerts = compilePrepared(spec, clean, policy)
    attachEvidence(spec, alerts, clean)
  }

  def compilePrepared(spec: CompiledSpec, clean: DataFrame, policy: Option[DataFrame]): DataFrame =
    spec.recipe match {
      case "SequenceThenTrigger"       => sequenceThenTrigger(spec, clean)
      case "DistinctCountWithinWindow" => distinctCountWithinWindow(spec, clean)
      case "PolicyCompare" =>
        policyCompare(spec, clean, policy.getOrElse(
          throw new IllegalArgumentException(s"${spec.behaviourId}: recipe PolicyCompare requires a policy reference DataFrame")))
      case other =>
        throw new IllegalArgumentException(
          s"${spec.behaviourId}: unknown recipe '$other' — compiler only implements the 3 recipes in docs/spec/compiled-spec-format.md")
    }

  private def sequenceThenTrigger(spec: CompiledSpec, events: DataFrame): DataFrame = {
    val groupCol    = spec.groupingKey.get
    val windowMicros = spec.timeWindowSeconds.get * 1000000L
    val countType   = spec.countEventType.get
    val threshold   = spec.countThreshold.get
    val triggerType = spec.triggerEventType.get

    // Closed interval [t - W, t] in microseconds; peers with an identical timestamp are all inside the frame.
    val byGroupTime = Window
      .partitionBy(col(groupCol))
      .orderBy(col("ts_micros"))
      .rangeBetween(-windowMicros, 0L)

    events
      .withColumn("recent_count", sum(when(col("event_type") === countType, 1).otherwise(0)).over(byGroupTime))
      .filter(col("event_type") === triggerType)
      .filter(col("recent_count") >= threshold)
      .select(
        col(groupCol).as("groupKey"),
        col("event_id").as("triggeringEventId"),
        col("timestamp").as("detectedAt"),
        col("ts_micros").as("detectedAtMicros"),
        renderMicros(col("ts_micros") - lit(windowMicros)).as("windowStart"),
        col("recent_count").as("matchedCount"),
        lit(spec.behaviourId).as("behaviourId"),
        lit("alert").as("status"),
      )
  }

  private def distinctCountWithinWindow(spec: CompiledSpec, events: DataFrame): DataFrame = {
    val groupCol     = spec.groupingKey.get
    val windowMicros = spec.timeWindowSeconds.get * 1000000L
    val filterType   = spec.filterEventType.get
    val distinctCol  = spec.distinctField.get
    val threshold    = spec.distinctThreshold.get

    // A range-boundary window requires exactly ONE order-by expression (Spark rejects a range frame with a
    // tiebreaker, DATATYPE_MISMATCH.RANGE_FRAME_MULTI_ORDER); ties don't change its result since it is
    // value-ranged. `lag()` IS row-position based and needs a real tiebreaker (event_id) for a deterministic
    // order on a tied timestamp — the independent reference detector sorts by (timestamp, event_id) too.
    val byGroupTime       = Window.partitionBy(col(groupCol)).orderBy(col("ts_micros")).rangeBetween(-windowMicros, 0L)
    val orderByTimeThenId = Window.partitionBy(col(groupCol)).orderBy(col("ts_micros"), col("event_id"))

    // One alert per INCIDENT: the instant the rolling distinct count first reaches the threshold (a "rising
    // edge"), the same per-trigger granularity SequenceThenTrigger has (detection-semantics.md case E).
    // Exact distinct counts via collect_set + size: this is a security threshold comparison, and
    // approx_count_distinct's HyperLogLog error would change which incidents alert.
    val breaching = col("recent_distinct") >= threshold

    events
      .filter(col("event_type") === filterType)
      .withColumn("recent_distinct", size(collect_set(col(distinctCol)).over(byGroupTime)))
      // a window function must be materialised via withColumn before it can be filtered on
      .withColumn("breaching", breaching)
      .withColumn("previouslyBreaching", lag(col("breaching"), 1, false).over(orderByTimeThenId))
      .filter(col("breaching") && !col("previouslyBreaching"))
      .select(
        col(groupCol).as("groupKey"),
        col("event_id").as("triggeringEventId"),
        col("timestamp").as("detectedAt"),
        col("ts_micros").as("detectedAtMicros"),
        renderMicros(col("ts_micros") - lit(windowMicros)).as("windowStart"),
        col("recent_distinct").as("matchedCount"),
        lit(spec.behaviourId).as("behaviourId"),
        lit("alert").as("status"),
      )
  }

  /** One row per account: identical duplicates collapse, conflicting ones are flagged and their policy value nulled. */
  private def dedupePolicy(policy: DataFrame, policyField: String): DataFrame = {
    val versionCol = if (policy.columns.contains("policy_version")) col("policy_version").cast("string") else lit(null).cast("string")
    policy
      .filter(col("account_id").isNotNull)
      .groupBy("account_id")
      .agg(
        countDistinct(col(policyField)).as("_distinct_values"),
        max(when(col(policyField).isNull, 1).otherwise(0)).as("_has_null"),
        first(col(policyField), ignoreNulls = true).as("_value"),
        countDistinct(versionCol).as("_distinct_versions"),
        first(versionCol, ignoreNulls = true).as("_version"),
      )
      .select(
        col("account_id"),
        lit(true).as("_has_policy"),
        (col("_distinct_values") + col("_has_null") > 1).as("_policy_conflict"),
        when(col("_distinct_values") + col("_has_null") > 1, lit(null)).otherwise(col("_value")).as(policyField),
        when(col("_distinct_values") + col("_has_null") > 1, lit(null).cast("string"))
          .when(col("_distinct_versions") === 1, col("_version")).as("policyVersion"),
      )
  }

  /** Events joined to the policy row IN FORCE AT EACH EVENT'S TIMESTAMP. Used when the policy carries an
    * `effective_from` column: the latest row whose effective_from is at or before the event applies, so a policy
    * change is not retroactive and does not have to arrive before the events it governs. Rows sharing the latest
    * effective_from either agree or conflict (-> insufficient_context). A row with an unparseable (non-null)
    * effective_from is ignored; a null one is treated as "always in force". Each result records the
    * `policy_version` it used. Output columns match `dedupePolicy`'s join. */
  private def joinVersionedPolicy(events: DataFrame, policy: DataFrame, policyField: String): DataFrame = {
    val parsed = parseMicros(col("effective_from").cast("string"))
    val p = policy.filter(col("account_id").isNotNull)
      .filter(col("effective_from").isNull || parsed.isNotNull)
      .select(
        col("account_id").as("p_account"),
        coalesce(parsed, lit(Long.MinValue)).as("p_eff"),
        col(policyField).as("p_value"),
        (if (policy.columns.contains("policy_version")) col("policy_version").cast("string") else lit(null).cast("string")).as("p_version"))
    val joined = events.join(p, events("account_id") === p("p_account") && p("p_eff") <= events("ts_micros"), "left")
    val inForce = joined
      .withColumn("_max_eff", max("p_eff").over(Window.partitionBy("event_id")))
      .filter(col("p_eff").isNull || col("p_eff") === col("_max_eff"))
    val perEvent = inForce.groupBy("event_id").agg(
      count(when(col("p_account").isNotNull, 1)).as("_n"),
      countDistinct(col("p_value")).as("_dv"),
      max(when(col("p_value").isNull && col("p_account").isNotNull, 1).otherwise(0)).as("_hn"),
      first(col("p_value"), ignoreNulls = true).as("_val"),
      countDistinct(col("p_version")).as("_dver"),
      first(col("p_version"), ignoreNulls = true).as("_ver"))
    events.join(perEvent, Seq("event_id"), "left")
      .withColumn("_has_policy", when(col("_n") > 0, lit(true)))
      .withColumn("_policy_conflict", col("_dv") + col("_hn") > 1)
      .withColumn(policyField, when(col("_dv") + col("_hn") > 1, lit(null)).otherwise(col("_val")))
      .withColumn("policyVersion", when(col("_dv") + col("_hn") > 1, lit(null).cast("string")).when(col("_dver") === 1, col("_ver")))
      .drop("_n", "_dv", "_hn", "_val", "_dver", "_ver")
  }

  private def policyCompare(spec: CompiledSpec, events: DataFrame, policy: DataFrame): DataFrame = {
    val filterType  = spec.filterEventType.get
    val logField    = spec.logField.get
    val policyField = spec.policyField.get
    val op          = spec.comparisonOp.get

    val comparisonExpr: Column = op match {
      case "notEqual"          => col(logField) =!= col(policyField)
      case "falseWhenRequired" => col(policyField) === true && col(logField) === false
      case other =>
        throw new IllegalArgumentException(s"${spec.behaviourId}: unknown comparisonOp '$other'")
    }
    // Three-valued SQL logic makes both comparisons NULL when either side is NULL, which `.otherwise`
    // would read as "condition not met" — an unobserved value silently treated as compliant. Every NULL and
    // every unusable policy state is therefore its own explicit `insufficient_context` with a reason.
    val statusExpr: Column =
      when(col("_has_policy").isNull, lit("insufficient_context"))
        .when(col("_policy_conflict"), lit("insufficient_context"))
        .when(col(policyField).isNull, lit("insufficient_context"))
        .when(col(logField).isNull, lit("insufficient_context"))
        .when(comparisonExpr, lit("alert"))
        .otherwise(lit("no_alert"))
    val reasonExpr: Column =
      when(col("_has_policy").isNull, lit("no_policy_record"))
        .when(col("_policy_conflict"), lit("policy_conflict"))
        .when(col(policyField).isNull, lit("policy_value_null"))
        .when(col(logField).isNull, lit("log_value_null"))
        .when(comparisonExpr, lit("policy_violated"))
        .otherwise(lit("compliant"))

    val relevant = events.filter(col("event_type") === filterType)
    val withPolicy =
      if (policy.columns.contains("effective_from")) joinVersionedPolicy(relevant, policy, policyField)
      else relevant.join(dedupePolicy(policy, policyField), Seq("account_id"), "left")

    withPolicy
      .withColumn("status", statusExpr)
      .withColumn("reason", reasonExpr)
      .select(
        col("account_id").as("groupKey"),
        col("event_id").as("triggeringEventId"),
        col("timestamp").as("detectedAt"),
        col("ts_micros").as("detectedAtMicros"),
        col(logField).as("observedValue"),
        col(policyField).as("expectedValue"),
        col("policyVersion"),
        col("status"),
        col("reason"),
        lit(spec.behaviourId).as("behaviourId"),
      )
  }

  /** Adds `evidence`: for windowed recipes the events inside [t - W, t] that made the rule fire, ordered by
    * time. Done as a second pass over the (few) alerts joined back to events, not as a `collect_list` over
    * every row's window — that would build a list for every one of millions of events only to discard
    * nearly all of them. */
  def attachEvidence(spec: CompiledSpec, alerts: DataFrame, clean: DataFrame): DataFrame = {
    spec.recipe match {
      case "PolicyCompare" => alerts
      case _ =>
        val windowMicros = spec.timeWindowSeconds.get * 1000000L
        val (groupCol, evType, valueCol) = spec.recipe match {
          case "SequenceThenTrigger" => (spec.groupingKey.get, spec.countEventType.get, "event_type")
          case _                     => (spec.groupingKey.get, spec.filterEventType.get, spec.distinctField.get)
        }
        val keys = alerts.select(col("groupKey"), col("triggeringEventId"), col("detectedAtMicros"))
        val supporting = clean.filter(col("event_type") === evType).select(
          col(groupCol).as("groupKey"), col("ts_micros").as("_e_ts"),
          col("event_id").as("_e_id"), col("timestamp").as("_e_time"), col(valueCol).cast("string").as("_e_value"))
        val evidence = broadcast(keys).join(supporting, Seq("groupKey"))
          .filter(col("_e_ts") >= col("detectedAtMicros") - lit(windowMicros) && col("_e_ts") <= col("detectedAtMicros"))
          .groupBy("groupKey", "triggeringEventId")
          .agg(sort_array(collect_list(struct(
            col("_e_ts").as("tsMicros"), col("_e_id").as("eventId"), col("_e_time").as("timestamp"), col("_e_value").as("value")
          ))).as("evidence"))
        alerts.join(evidence, Seq("groupKey", "triggeringEventId"), "left")
    }
  }
}
