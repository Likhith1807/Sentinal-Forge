import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

object MFARequiredBypassDetector {

  /**
   * Detects login_success events where MFA was not used for accounts that
   * are required to use MFA according to an external policy reference.
   *
   * The policy reference must be available as a table or temporary view named
   * "account_policy" with the following columns:
   *   - account_id (string)
   *   - mfa_required (boolean)
   *
   * Accounts absent from the policy reference are ignored (no alert generated).
   *
   * @param events DataFrame containing authentication log events.
   * @return DataFrame of alerts, preserving original event fields and adding an `alert_type` column.
   */
  def detect(events: DataFrame): DataFrame = {
    val spark: SparkSession = events.sparkSession
    import spark.implicits._

    // Load the external policy reference
    val policy = spark.table("account_policy")
      .select($"account_id".as("policy_account_id"), $"mfa_required")

    // Filter for successful logins without MFA and join with policy requiring MFA
    val alerts = events
      .filter($"event_type" === "login_success" && $"mfa_used" === false)
      .join(policy, events("account_id") === policy("policy_account_id"), "inner")
      .filter($"mfa_required" === true)
      .withColumn("alert_type", lit("MFA_REQUIRED_BYPASS"))
      .drop("policy_account_id")

    alerts
  }
}
