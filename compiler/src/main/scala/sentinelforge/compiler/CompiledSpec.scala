package sentinelforge.compiler

import org.apache.spark.sql.SparkSession

/** See docs/spec/compiled-spec-format.md. Flat, closed shape — every field
  * beyond `behaviourId`/`recipe` is optional because each recipe only uses
  * a subset, never because the format itself is open-ended.
  *
  * `ruleHash` / `compilerVersion` / `countSemantics` are provenance written by the Python compiler
  * (sentinelforge.compile); the executors copy them into every run record so an alert can be traced
  * to the exact rule that produced it. They are optional so hand-authored specs keep loading.
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
  ruleHash: Option[String] = None,
  compilerVersion: Option[String] = None,
  countSemantics: Option[String] = None,
)

object CompiledSpec {

  /** Loaded via spark.read.json rather than a JSON library dependency —
    * this is a Spark program already, and the format is one flat object
    * per file (`multiLine` so a pretty-printed file also loads). */
  def load(spark: SparkSession, path: String): CompiledSpec = {
    val row = spark.read.option("multiLine", "true").json(path).first()
    val names = row.schema.fieldNames.toSet

    def strOpt(name: String): Option[String] =
      if (!names.contains(name) || row.isNullAt(row.fieldIndex(name))) None else Some(row.getAs[String](name))

    def longOpt(name: String): Option[Long] =
      if (!names.contains(name) || row.isNullAt(row.fieldIndex(name))) None else Some(row.getAs[Long](name))

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
      ruleHash = strOpt("ruleHash"),
      compilerVersion = strOpt("compilerVersion"),
      countSemantics = strOpt("countSemantics"),
    )
  }
}
