import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

object TokenOnlyServiceAccountDetection {

  // Policy reference: accounts that are allowed token‑only authentication
  private val tokenOnlyAccounts = Set(
    "svc-notify09" // add additional account IDs here as needed
  )

  /**
   * Detects successful logins where the authentication method does not match
   * the expected token‑only method for the accounts listed in the policy.
   *
   * @param events DataFrame containing authentication events with columns:
   *               - event_type (String)
   *               - auth_method (String)
   *               - account_id (String)
   * @return DataFrame of alerts (subset of the input rows that match the rule)
   */
  def detect(events: DataFrame): DataFrame = {
    events
      .filter(
        col("event_type") === "login_success" &&
        col("account_id").isin(tokenOnlyAccounts.toSeq: _*) &&
        col("auth_method") =!= "token"
      )
  }
}