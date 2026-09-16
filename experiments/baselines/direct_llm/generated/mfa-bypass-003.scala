import org.apache.spark.sql.{DataFrame, SparkSession, functions => F}
import org.apache.spark.sql.types._

object MFABypassDetection {

  // Hard‑coded list of accounts that are known to have MFA required by policy.
  // In a real deployment this would be loaded from a policy reference source.
  private val mfaRequiredAccounts: Set[String] = Set(
    "dpatel" // example account from the threat report
    // add other accounts as needed
  )

  /**
   * Detects successful logins where MFA was not used for accounts that are
   * required to use MFA.
   *
   * @param events authentication log DataFrame adhering to the authentication_log_schema.json
   * @return DataFrame containing the matching alert events
   */
  def detect(events: DataFrame): DataFrame = {
    // Ensure the required columns exist – schema validation is handled upstream.
    val filtered = events
      .filter(
        (F.col("event_type") === "login_success") &&
        (F.col("mfa_used") === false) &&
        F.col("account_id").isin(mfaRequiredAccounts.toSeq: _*)
      )
      .withColumn("alert_name", F.lit("MFA_BYPASS_DETECTED"))
      // Include only relevant fields in the alert output
      .select(
        F.col("event_id"),
        F.col("timestamp"),
        F.col("account_id"),
        F.col("source_host"),
        F.col("auth_method"),
        F.col("alert_name")
      )

    filtered
  }
}
