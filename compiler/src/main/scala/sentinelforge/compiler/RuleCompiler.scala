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

    val byGroupTime = Window
      .partitionBy(col(groupCol))
      .orderBy(parseTs(col("timestamp")).cast("long"))
      .rangeBetween(-windowSecs, 0L)

    events
      .filter(col("event_type") === filterType)
      .withColumn("recent_distinct", approx_count_distinct(col(distinctCol)).over(byGroupTime))
      .filter(col("recent_distinct") >= threshold)
      .groupBy(col(groupCol).as("groupKey"))
      .agg(
        min(col("timestamp")).as("windowStart"),
        max(col("timestamp")).as("detectedAt"),
        max(col("recent_distinct")).as("matchedCount"),
      )
      .select(
        col("groupKey"), col("detectedAt"), col("windowStart"), col("matchedCount"),
        lit(spec.behaviourId).as("behaviourId"),
        lit("alert").as("status"),
      )
  }

  private def policyCompare(spec: CompiledSpec, events: DataFrame, policy: DataFrame): DataFrame = {
    val filterType  = spec.filterEventType.get
    val logField    = spec.logField.get
    val policyField = spec.policyField.get
    val op          = spec.comparisonOp.get

    val statusExpr: Column = op match {
      case "notEqual" =>
        when(col(policyField).isNull, lit("insufficient_context"))
          .when(col(logField) =!= col(policyField), lit("alert"))
          .otherwise(lit("no_alert"))
      case "falseWhenRequired" =>
        when(col(policyField).isNull, lit("insufficient_context"))
          .when(col(policyField) === true && col(logField) === false, lit("alert"))
          .otherwise(lit("no_alert"))
      case other =>
        throw new IllegalArgumentException(s"${spec.behaviourId}: unknown comparisonOp '$other'")
    }

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
