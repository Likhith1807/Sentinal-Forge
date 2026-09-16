package sentinelforge.compiler

import org.apache.spark.sql.SparkSession

/** See docs/spec/compiled-spec-format.md. Flat, closed shape — every field
  * beyond `behaviourId`/`recipe` is optional because each recipe only uses
  * a subset, never because the format itself is open-ended.
  */
case class CompiledSpec(
  behaviourId: String,
  recipe: String,
  groupingKey: Option[String],
  timeWindowSeconds: Option[Long],
  countEventType: Option[String],
  countThreshold: Option[Long],
  triggerEventType: Option[String],
  distinctField: Option[String],
  distinctThreshold: Option[Long],
  filterEventType: Option[String],
  logField: Option[String],
  policyField: Option[String],
  comparisonOp: Option[String],
)

object CompiledSpec {

  /** Loaded via spark.read.json rather than a JSON library dependency —
    * this is a Spark program already, and the format is one flat object
    * per file. */
  def load(spark: SparkSession, path: String): CompiledSpec = {
    val row = spark.read.json(path).first()

    def strOpt(name: String): Option[String] =
      if (row.isNullAt(row.fieldIndex(name))) None else Some(row.getAs[String](name))

    def longOpt(name: String): Option[Long] =
      if (row.isNullAt(row.fieldIndex(name))) None else Some(row.getAs[Long](name))

    CompiledSpec(
      behaviourId = row.getAs[String]("behaviourId"),
      recipe = row.getAs[String]("recipe"),
      groupingKey = strOpt("groupingKey"),
      timeWindowSeconds = longOpt("timeWindowSeconds"),
      countEventType = strOpt("countEventType"),
      countThreshold = longOpt("countThreshold"),
      triggerEventType = strOpt("triggerEventType"),
      distinctField = strOpt("distinctField"),
      distinctThreshold = longOpt("distinctThreshold"),
      filterEventType = strOpt("filterEventType"),
      logField = strOpt("logField"),
      policyField = strOpt("policyField"),
      comparisonOp = strOpt("comparisonOp"),
    )
  }
}
