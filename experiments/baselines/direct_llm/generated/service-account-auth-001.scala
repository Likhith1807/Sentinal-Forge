import org.apache.spark.sql.{DataFrame, SparkSession, functions => F}
import org.apache.spark.sql.expressions.Window

object ServiceAccountAuthMismatch {

  /**
   * Detects successful logins where the authentication method used does not
   * match the expected method defined in the account policy reference.
   *
   * @param events DataFrame containing authentication log events (schema v1).
   * @return DataFrame of alerts with relevant context.
   */
  def detect(events: DataFrame): DataFrame = {
    // Spark session from the input DataFrame
    val spark: SparkSession = events.sparkSession
    import spark.implicits._

    // ------------------------------------------------------------------
    // Load the policy reference.
    // It must contain at least: account_id (string), expected_auth_method (string)
    // The table/view name "account_policy" is assumed to be available in the
    // Spark catalog. Adjust the source as needed for your environment.
    // ------------------------------------------------------------------
    val policy = spark.table("account_policy")
      .select($"account_id".as("policy_account_id"), $"expected_auth_method")

    // ------------------------------------------------------------------
    // Join authentication events with the policy reference on account_id.
    // Keep only accounts that have a policy entry (inner join).
    // ------------------------------------------------------------------
    val joined = events
      .filter($"event_type" === "login_success")
      .join(policy,
        events("account_id") === policy("policy_account_id"),
        "inner"
      )
      .filter($"auth_method" =!= $"expected_auth_method")

    // ------------------------------------------------------------------
    // Build the alert output.
    // Columns:
    //   alert_id          – use the original event_id for traceability
    //   timestamp
    //   account_id
    //   source_host
    //   auth_method
    //   expected_auth_method
    //   alert_reason      – human‑readable description
    // ------------------------------------------------------------------
    val alerts = joined.withColumn(
      "alert_reason",
      F.concat(
        F.lit("Authentication method mismatch for account "),
        $"account_id",
        F.lit(": used "),
        $"auth_method",
        F.lit(", expected "),
        $"expected_auth_method"
      )
    ).select(
      $"event_id".as("alert_id"),
      $"timestamp",
      $"account_id",
      $"source_host",
      $"auth_method",
      $"expected_auth_method",
      $"alert_reason"
    )

    alerts
  }
}
