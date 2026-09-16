package com.sentinelforge.rules

import org.apache.spark.sql.{DataFrame, SparkSession, functions => F}
import org.apache.spark.sql.expressions.Window

object ConcurrentSessionDetection {

  /**
   * Detects accounts that have two or more successful logins from different source hosts
   * within a 15‑minute sliding window.
   *
   * @param events Spark DataFrame adhering to the authentication_log_schema.json schema
   * @return DataFrame of alerts containing:
   *         - account_id : String
   *         - window_start : Timestamp (start of the 15‑min window)
   *         - window_end   : Timestamp (end of the 15‑min window)
   *         - hosts        : Array[String] (distinct source_host values observed)
   */
  def detect(events: DataFrame)(implicit spark: SparkSession): DataFrame = {
    import spark.implicits._

    // Keep only successful login events
    val successes = events
      .filter($"event_type" === "login_success")
      .select(
        $"event_id",
        $"timestamp".cast("timestamp").as("ts"),
        $"account_id",
        $"source_host"
      )

    // 15‑minute sliding window, 1‑minute slide (granular enough for the use‑case)
    val windowed = successes
      .groupBy(
        $"account_id",
        F.window($"ts", "15 minutes", "1 minute")
      )
      .agg(
        F.countDistinct($"source_host").as("distinct_host_cnt"),
        F.collect_set($"source_host").as("hosts")
      )
      .filter($"distinct_host_cnt" >= 2)
      .select(
        $"account_id",
        $"window.start".as("window_start"),
        $"window.end".as("window_end"),
        $"hosts"
      )

    windowed
  }
}
