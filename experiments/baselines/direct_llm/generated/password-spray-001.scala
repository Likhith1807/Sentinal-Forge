package com.sentinelforge.detection

import org.apache.spark.sql.{DataFrame, SparkSession, Column}
import org.apache.spark.sql.functions._
import org.apache.spark.sql.expressions.Window

object PasswordSprayDetector {

  /**
   * Detect password‑spraying activity.
   *
   * @param logs DataFrame containing authentication events that conform to the
   *             authentication_log_schema (v1).
   * @return DataFrame of alerts with the following columns:
   *         - source_host (string)
   *         - window_start (timestamp)
   *         - window_end   (timestamp)
   *         - distinct_account_count (int)
   *         - accounts (array<string>)  // the distinct accounts observed
   */
  def detect(logs: DataFrame): DataFrame = {
    // Convert the ISO‑8601 string timestamp to a true Timestamp column
    val logsWithTs = logs
      .withColumn("event_ts", to_timestamp(col("timestamp")))

    // Keep only failed login attempts
    val failures = logsWithTs.filter(col("event_type") === lit("login_failure"))

    // Sliding window: 10‑minute duration, 1‑minute slide (adjust slide if desired)
    val windowed = failures
      .groupBy(
        window(col("event_ts"), "10 minutes", "1 minute"),
        col("source_host")
      )
      .agg(
        collect_set(col("account_id")).as("accounts")
      )
      .withColumn("distinct_account_count", size(col("accounts")))
      .filter(col("distinct_account_count") >= lit(4))

    // Shape the alert output
    windowed.select(
      col("source_host"),
      col("window.start").as("window_start"),
      col("window.end").as("window_end"),
      col("distinct_account_count"),
      col("accounts")
    )
  }
}
