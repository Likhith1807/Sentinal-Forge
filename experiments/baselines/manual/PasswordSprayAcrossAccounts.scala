package sentinelforge.baselines.manual

/*
 * MANUAL-RULES BASELINE — hand-authored directly from reading
 * data/samples/reports/password-spray-001.md, with no NLP extraction, no
 * typed IR, and no automated schema check. This is what an analyst would
 * write on their own; it exists to give Phase 5's benchmark a real "no
 * tooling at all" comparison point, not a strawman.
 */

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions._

object PasswordSprayAcrossAccounts {

  final val DistinctAccountThreshold = 4
  final val WindowMinutes = 10

  def detect(spark: SparkSession, events: DataFrame): DataFrame = {
    import spark.implicits._

    // See compiler/test/golden/LoginBruteForceThenSuccess.scala's note on
    // this exact bug (raw string cast to long instead of parsed).
    // password-spray's own 2 replay scenarios don't happen to expose it
    // (neither depends on a failure falling outside the window while
    // still being "recent" by account count), but the bug is the same
    // latent defect — fixed proactively rather than left in place because
    // this specific test set doesn't catch it.
    val parsedTs = to_timestamp($"timestamp", "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'").cast("long")

    val byHostTime = Window
      .partitionBy($"source_host")
      .orderBy(parsedTs)
      .rangeBetween(-WindowMinutes * 60L, 0L)

    events
      .filter($"event_type" === "login_failure")
      .withColumn(
        "distinct_accounts_recent",
        approx_count_distinct($"account_id").over(byHostTime)
      )
      .filter($"distinct_accounts_recent" >= DistinctAccountThreshold)
      .groupBy($"source_host")
      .agg(
        min($"timestamp").as("first_event_at"),
        max($"timestamp").as("triggered_at"),
        max($"distinct_accounts_recent").as("distinct_accounts")
      )
      .withColumn("behaviour_id", lit("password-spray-across-accounts"))
  }
}
