import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions.lit

object ImpossibleTravelDetector {
  /**
   * Detects impossible‑travel login events.
   *
   * The required geolocation field is not available in the validated schema,
   * therefore this implementation cannot produce any alerts and returns an
   * empty DataFrame with the expected alert columns.
   *
   * @param events DataFrame containing authentication events.
   * @return DataFrame of alerts (empty in this case).
   */
  def detect(events: DataFrame): DataFrame = {
    // Preserve the expected alert schema (e.g., account_id, event_type)
    // but produce no rows because the necessary geolocation data is unavailable.
    events
      .filter(lit(false))                     // forces an empty result set
      .select(
        events.col("account_id"),
        events.col("event_type")
      )
  }
}