import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._
import org.apache.spark.sql.expressions.Window

object BruteForceDetection {
  def detect(events: DataFrame): DataFrame = {
    // Convert timestamp to epoch seconds for range-based window
    val eventsTs = events
      .withColumn("ts", unix_timestamp(col("timestamp")).cast("long"))

    // Sliding window of 2 minutes (120 seconds) per account
    val w = Window
      .partitionBy(col("account_id"))
      .orderBy(col("ts"))
      .rangeBetween(-120, 0)   // look back 2 minutes

    // Count failures in the window
    val withFailCount = eventsTs.withColumn(
      "failure_count",
      sum(when(col("event_type") === "failure", 1).otherwise(0)).over(w)
    )

    // Alert on a success that follows ≥5 failures within the window
    withFailCount
      .filter(col("event_type") === "success" && col("failure_count") >= 5)
      .select(col("account_id"), col("timestamp"))
  }
}