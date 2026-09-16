import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

object ThreatDetection {
  /**
   * Detects authentication events where a successful login occurred
   * without MFA being used.
   *
   * @param events DataFrame containing authentication events.
   *               Expected columns: event_type (String), mfa_used (Boolean)
   * @return DataFrame of matching alerts.
   */
  def detect(events: DataFrame): DataFrame = {
    events.filter(
      col("event_type") === lit("login_success") &&
      col("mfa_used") === lit(false)
    )
  }
}