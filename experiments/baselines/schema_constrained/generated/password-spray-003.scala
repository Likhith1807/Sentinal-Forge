import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

object CredentialSprayingDetector {
  def detect(events: DataFrame): DataFrame = {
    // Filter to failed authentication events
    val failures = events.filter(col("event_type") === "failure")

    // Aggregate distinct accounts per source host in a 10‑minute sliding window
    val alerts = failures
      .groupBy(
        col("source_host"),
        window(col("timestamp"), "10 minutes", "1 minute")
      )
      .agg(countDistinct(col("account_id")).alias("distinct_account_count"))
      .filter(col("distinct_account_count") >= 4)
      .select(
        col("source_host"),
        col("window.start").alias("window_start"),
        col("window.end").alias("window_end"),
        col("distinct_account_count")
      )

    alerts
  }
}