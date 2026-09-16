import org.apache.spark.sql.{DataFrame, SparkSession, functions => F}
import org.apache.spark.sql.expressions.Window

object ThreatDetection {

  /**
   * Detects accounts with two or more successful logins from different hosts
   * within a 15‑minute sliding window.
   *
   * @param events DataFrame containing authentication events with the fields:
   *               timestamp (timestamp), account_id (string), event_type (string),
   *               source_host (string)
   * @return DataFrame of alerts. Columns:
   *         account_id, first_timestamp, second_timestamp, host1, host2
   */
  def detect(events: DataFrame): DataFrame = {
    // Keep only successful login events
    val successful = events.filter(F.col("event_type") === "login_success")

    // Alias for self‑join
    val a = successful.alias("a")
    val b = successful.alias("b")

    // Join on same account, different hosts, timestamps within 15 minutes
    val joined = a.join(b,
      F.col("a.account_id") === F.col("b.account_id") &&
      F.col("a.source_host") =!= F.col("b.source_host") &&
      F.col("a.timestamp") < F.col("b.timestamp") &&
      F.col("b.timestamp") <= F.col("a.timestamp") + F.expr("interval 15 minutes")
    )

    // Produce distinct alerts (order the timestamps to avoid duplicates)
    val alerts = joined.select(
      F.col("a.account_id").as("account_id"),
      F.least(F.col("a.timestamp"), F.col("b.timestamp")).as("first_timestamp"),
      F.greatest(F.col("a.timestamp"), F.col("b.timestamp")).as("second_timestamp"),
      F.col("a.source_host").as("host1"),
      F.col("b.source_host").as("host2")
    ).distinct()

    alerts
  }
}