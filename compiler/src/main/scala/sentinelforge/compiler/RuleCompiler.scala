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
  */
object RuleCompiler {

  /** authentication_log_schema.json's timestamp values are always
    * "yyyy-MM-ddTHH:mm:ss.SSSZ" with a literal trailing Z (fixed UTC, not
    * a variable offset) — parsed explicitly rather than relying on
    * to_timestamp's format-guessing, which is exactly the kind of silent
    * assumption this project's whole premise argues against. */
  private def parseTs(c: Column): Column =
    to_timestamp(c, "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'")

  def compile(spec: CompiledSpec, events: DataFrame, policy: Option[DataFrame] = None): DataFrame =
    spec.recipe match {
      case "SequenceThenTrigger"       => sequenceThenTrigger(spec, events)
      case "DistinctCountWithinWindow" => distinctCountWithinWindow(spec, events)
      case "PolicyCompare" =>
        policyCompare(spec, events, policy.getOrElse(
          throw new IllegalArgumentException(s"${spec.behaviourId}: recipe PolicyCompare requires a policy reference DataFrame")))
      case other =>
        throw new IllegalArgumentException(
          s"${spec.behaviourId}: unknown recipe '$other' — compiler only implements the 3 recipes in docs/spec/compiled-spec-format.md")
    }

  private def sequenceThenTrigger(spec: CompiledSpec, events: DataFrame): DataFrame = {
    val groupCol    = spec.groupingKey.get
    val windowSecs  = spec.timeWindowSeconds.get
    val countType   = spec.countEventType.get
    val threshold   = spec.countThreshold.get
    val triggerType = spec.triggerEventType.get

    val byGroupTime = Window
      .partitionBy(col(groupCol))
      .orderBy(parseTs(col("timestamp")).cast("long"))
      .rangeBetween(-windowSecs, 0L)

    events
      .withColumn("ts_long", parseTs(col("timestamp")).cast("long"))
      .withColumn("recent_count", sum(when(col("event_type") === countType, 1).otherwise(0)).over(byGroupTime))
      .filter(col("event_type") === triggerType)
      .filter(col("recent_count") >= threshold)
      .select(
        col(groupCol).as("groupKey"),
        col("event_id").as("triggeringEventId"),
        col("timestamp").as("detectedAt"),
        col("recent_count").as("matchedCount"),
        lit(spec.behaviourId).as("behaviourId"),
        lit("alert").as("status"),
      )
  }

  private def distinctCountWithinWindow(spec: CompiledSpec, events: DataFrame): DataFrame = {
    val groupCol     = spec.groupingKey.get
    val windowSecs   = spec.timeWindowSeconds.get
    val filterType   = spec.filterEventType.get
    val distinctCol  = spec.distinctField.get
    val threshold    = spec.distinctThreshold.get

    // A range-boundary window (`rangeBetween` with a numeric offset, used for the rolling
    // collect_set below) requires exactly ONE order-by expression — Spark rejects a range frame
    // with a tiebreaker column outright (DATATYPE_MISMATCH.RANGE_FRAME_MULTI_ORDER), so that one
    // stays single-key; ties don't change its result anyway, since it's value-ranged, not
    // row-position-based (case C, docs/spec/detection-semantics.md). `lag()` below IS row-position
    // based and needs its own window with a real tiebreaker (event_id) for deterministic row
    // order on a tied timestamp — scripts/datagen/refdetect.py's independent reference
    // implementation breaks ties the same way (sorts by (second, event_id)), so the two agree.
    val byGroupTime = Window.partitionBy(col(groupCol)).orderBy(col("ts_long")).rangeBetween(-windowSecs, 0L)
    val orderByTimeThenId = Window.partitionBy(col(groupCol)).orderBy(col("ts_long"), col("event_id"))

    // CORRECTION (Phase D, docs/spec/detection-semantics.md case E): this
    // used to groupBy(groupCol) and collapse EVERY qualifying row across
    // the WHOLE dataset into one output row per groupKey ever — two
    // separate incidents for the same host, a day apart, produced a single
    // alert whose windowStart/detectedAt misleadingly spanned the full day.
    // Fixed to alert once per INCIDENT: the instant the rolling distinct
    // count first reaches the threshold (a "rising edge" in the boolean
    // breaching signal), the same per-trigger-event granularity
    // SequenceThenTrigger already had, and exactly the "episode" semantics
    // scripts/datagen/refdetect.py's independent reference implementation
    // uses (built before this fix, from the intended — not the buggy —
    // semantics).
    //
    // Exact distinct count, not approx_count_distinct: this is a security
    // threshold comparison (matchedCount >= threshold decides whether an
    // alert fires), and Spark's HyperLogLog-based approximation carries a
    // real (if usually small) error rate. Spark's window-function
    // framework doesn't support `count(distinct x)` as a window aggregate
    // directly (only certain aggregates, approx_count_distinct among
    // them, are allowed `.over(window)`), so collect_set — which IS a
    // valid window aggregate — is used to materialize the exact distinct
    // set per row, and size() turns it into an exact count.
    val breaching = col("recent_distinct") >= threshold

    events
      .filter(col("event_type") === filterType)
      .withColumn("ts_long", parseTs(col("timestamp")).cast("long"))
      .withColumn("recent_distinct", size(collect_set(col(distinctCol)).over(byGroupTime)))
      // A window function (lag) must be materialized via withColumn before it can be filtered
      // on — Spark rejects a window-function expression used directly inside .filter()/.where()
      // ("not allowed to use window functions inside WHERE clause"), since WHERE logically runs
      // before a query's window-function evaluation stage.
      .withColumn("breaching", breaching)
      .withColumn("previouslyBreaching", lag(col("breaching"), 1, false).over(orderByTimeThenId))
      .filter(col("breaching") && !col("previouslyBreaching"))
      // from_unixtime, not to_timestamp: the input here is already a whole-second Long
      // (ts_long - windowSecs), not a string to parse — from_unixtime formats a Long epoch
      // directly. ".SSS" renders "000" since ts_long has no sub-second component, matching
      // this project's existing whole-second precision (case C, detection-semantics.md).
      .withColumn("windowStart", from_unixtime(col("ts_long") - windowSecs, "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"))
      .select(
        col(groupCol).as("groupKey"),
        col("event_id").as("triggeringEventId"),
        col("timestamp").as("detectedAt"),
        col("windowStart"),
        col("recent_distinct").as("matchedCount"),
        lit(spec.behaviourId).as("behaviourId"),
        lit("alert").as("status"),
      )
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
    // CORRECTION (Phase D, docs/spec/detection-semantics.md case G): a
    // NULL policyField (no matching policy row) already correctly degraded
    // to insufficient_context. A NULL logField — the schema field is
    // present, but this specific event's value is unobserved — did not:
    // three-valued SQL logic makes both `col(logField) =!= col(policyField)`
    // and `col(logField) === false` evaluate to NULL when logField is NULL,
    // which `.otherwise(...)` then treated as "condition not met", silently
    // reading an unobserved value as compliant. Checked explicitly instead,
    // for BOTH comparisonOps (the same three-valued-logic gap existed in
    // `notEqual`, not only the `falseWhenRequired` case detection-semantics.md
    // happened to test first).
    val statusExpr: Column =
      when(col(policyField).isNull, lit("insufficient_context"))
        .when(col(logField).isNull, lit("insufficient_context"))
        .when(comparisonExpr, lit("alert"))
        .otherwise(lit("no_alert"))

    events
      .filter(col("event_type") === filterType)
      .join(policy, Seq("account_id"), "left")
      .withColumn("status", statusExpr)
      .select(
        col("account_id").as("groupKey"),
        col("event_id").as("triggeringEventId"),
        col("timestamp").as("detectedAt"),
        col(logField).as("observedValue"),
        col("status"),
        lit(spec.behaviourId).as("behaviourId"),
      )
  }
}
