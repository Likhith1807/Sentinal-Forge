import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

object CredentialSprayingDetector {

  /** Detects password‑spraying attempts:
    *
    *  - event_type = "login_failure"
    *  - at least 4 distinct account_id values
    *  - within any 10‑minute sliding window (1‑minute slide) per source_host
    *
    * Returns a DataFrame with the source host, window bounds and the count
    * of distinct accounts that triggered the alert.
    */
  def detect(events: DataFrame): DataFrame = {
    // Ensure we have a proper timestamp column
    val withTs = events
      .withColumn("event_ts", to_timestamp(col("timestamp")))
      .filter(col("event_type") === lit("login_failure"))

    // Aggregate per source_host over a 10‑minute sliding window
    val agg = withTs
      .groupBy(
        col("source_host"),
        window(col("event_ts"), "10 minutes", "1 minute")
      )
      .agg(countDistinct(col("account_id")).as("distinct_accounts"))
      .filter(col("distinct_accounts") >= 4)

    // Shape the alert output
    agg.select(
      col("source_host"),
      col("window.start").as("window_start"),
      col("window.end").as("window_end"),
      col("distinct_accounts")
    )
  }
}
