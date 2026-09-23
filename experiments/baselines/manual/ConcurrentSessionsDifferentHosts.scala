package sentinelforge.baselines.manual

/*
 * MANUAL-RULES BASELINE — hand-authored from
 * data/samples/reports/concurrent-sessions-001.md. See
 * PasswordSprayAcrossAccounts.scala for the role this file plays in the
 * Phase 5 benchmark.
 */

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions._

object ConcurrentSessionsDifferentHosts {

  final val WindowMinutes = 15

  def detect(spark: SparkSession, events: DataFrame): DataFrame = {
    import spark.implicits._

    // See compiler/test/golden/LoginBruteForceThenSuccess.scala's note:
    // casting the raw ISO-8601 string straight to long returns null for
    // every row instead of parsing it, silently breaking the window's
    // ORDER BY. Caught by CS-NEG-WINDOW in
    // data/samples/replay/concurrent_sessions_labels.json only once this
    // was actually run, not from reading the code across 4 prior phases.
    val parsedTs = to_timestamp($"timestamp", "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'").cast("long")

    val byAccountTime = Window
      .partitionBy($"account_id")
      .orderBy(parsedTs)
      .rangeBetween(-WindowMinutes * 60L, 0L)

    events
      .filter($"event_type" === "login_success")
      .withColumn(
        "distinct_hosts_recent",
        approx_count_distinct($"source_host").over(byAccountTime)
      )
      .filter($"distinct_hosts_recent" >= 2)
      .select(
        $"account_id",
        $"event_id".as("triggering_event_id"),
        $"timestamp".as("detected_at"),
        $"source_host",
        $"distinct_hosts_recent",
        lit("multi-host-authentication").as("behaviour_id")
      )
  }
}
