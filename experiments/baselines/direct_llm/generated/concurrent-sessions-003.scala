package com.sentinelforge.rules

import org.apache.spark.sql.{DataFrame, SparkSession, functions => F}
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.types.TimestampType

object SimultaneousAccessRule {

  /** Detects accounts with two or more successful MFA logins from different hosts
    * within a 15‑minute sliding window.
    *
    * Output columns:
    *   - account_id
    *   - first_event_id
    *   - first_timestamp
    *   - first_host
    *   - second_event_id
    *   - second_timestamp
    *   - second_host
    *   - alert_timestamp   (timestamp of the later login)
    */
  def detect(events: DataFrame)(implicit spark: SparkSession): DataFrame = {
    import spark.implicits._

    // Keep only successful, MFA‑used logins
    val success = events
      .filter($"event_type" === "login_success" && $"mfa_used" === true)
      .withColumn("ts_sec", F.unix_timestamp($"timestamp")) // seconds since epoch
      .select(
        $"event_id",
        $"account_id",
        $"timestamp".as("login_timestamp"),
        $"source_host",
        $"ts_sec"
      )
      .cache()

    // Self‑join to find a later login from a different host within 15 min (900 s)
    val joined = success.alias("a")
      .join(
        success.alias("b"),
        ($"a.account_id" === $"b.account_id") &&
        ($"a.source_host" =!= $"b.source_host") &&
        ($"a.ts_sec" < $"b.ts_sec") &&                     // earlier -> later
        ($"b.ts_sec" - $"a.ts_sec" <= 15 * 60)             // ≤ 15 min
      )
      // Ensure each pair appears only once (a as earlier event)
      .filter($"a.event_id" < $"b.event_id")
      .select(
        $"a.account_id",
        $"a.event_id".as("first_event_id"),
        $"a.login_timestamp".as("first_timestamp"),
        $"a.source_host".as("first_host"),
        $"b.event_id".as("second_event_id"),
        $"b.login_timestamp".as("second_timestamp"),
        $"b.source_host".as("second_host"),
        $"b.login_timestamp".as("alert_timestamp")
      )
      .distinct()

    joined
  }
}
