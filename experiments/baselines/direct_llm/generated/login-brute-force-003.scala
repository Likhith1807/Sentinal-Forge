import org.apache.spark.sql.{DataFrame, SparkSession, functions => F}
import org.apache.spark.sql.expressions.Window

object CredentialAccessDetector {

  /** Detects a series of ≥5 failed logins within a 2‑minute sliding window
    * that are followed by a successful login for the same account.
    *
    * @param events authentication log DataFrame adhering to the provided schema
    * @return DataFrame of alerts with columns:
    *         - account_id (String)
    *         - event_id   (String)   // the successful login event id
    *         - timestamp  (Timestamp) // time of the successful login
    *         - failure_count (Long)   // number of failures in the preceding window
    */
  def detect(events: DataFrame): DataFrame = {
    // Ensure timestamp is a proper Timestamp type for interval arithmetic
    val evts = events
      .withColumn("event_ts", F.to_timestamp(F.col("timestamp")))

    // Separate failures and successes
    val failures = evts
      .filter(F.col("event_type") === "login_failure")
      .select(
        F.col("account_id").as("acct"),
        F.col("event_ts").as("fail_ts")
      )

    val successes = evts
      .filter(F.col("event_type") === "login_success")
      .select(
        F.col("account_id").as("acct"),
        F.col("event_id").as("success_event_id"),
        F.col("event_ts").as("success_ts")
      )

    // Join each success with failures that occurred in the preceding 2‑minute window
    val joined = successes
      .join(failures, Seq("acct"))
      .where(
        F.col("fail_ts").between(
          F.col("success_ts") - F.expr("INTERVAL 2 MINUTES"),
          F.col("success_ts")
        )
      )
      .groupBy(
        F.col("acct"),
        F.col("success_event_id"),
        F.col("success_ts")
      )
      .agg(F.count("*").as("failure_count"))
      .filter(F.col("failure_count") >= 5)

    // Return alerts in a clean schema
    joined.select(
      F.col("acct").as("account_id"),
      F.col("success_event_id").as("event_id"),
      F.col("success_ts").as("timestamp"),
      F.col("failure_count")
    )
  }
}
