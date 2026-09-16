package com.sentinelforge.detections

import org.apache.spark.sql.{DataFrame, SparkSession, functions => F}
import org.apache.spark.sql.expressions.UserDefinedFunction

object TokenAuthMismatchDetector {

  // -------------------------------------------------------------------------
  // Policy reference: expected authentication method for each account.
  // Extend this map with additional accounts as needed.
  // -------------------------------------------------------------------------
  private val expectedAuthMethods: Map[String, String] = Map(
    "svc-notify09" -> "token"
    // add other accounts here, e.g. "svc-other" -> "token"
  )

  // UDF to retrieve the expected auth_method for a given account_id
  private val expectedAuthUDF: UserDefinedFunction = F.udf { (acct: String) =>
    expectedAuthMethods.getOrElse(acct, null)
  }

  /**
   * Detects successful logins where the authentication method does not match
   * the expected method defined in the policy reference.
   *
   * @param events DataFrame conforming to the Authentication Log Schema v1.
   * @return DataFrame containing alert rows. Original event columns are kept
   *         and an additional column `expected_auth_method` is added.
   */
  def detect(events: DataFrame): DataFrame = {
    // Add a column with the expected authentication method for the account
    val withExpected = events.withColumn(
      "expected_auth_method",
      expectedAuthUDF(F.col("account_id"))
    )

    // Filter to successful logins where the auth method mismatches the policy
    val alerts = withExpected.filter(
      F.col("event_type") === "login_success" &&                     // only successes
      F.col("expected_auth_method").isNotNull &&                    // account is in policy scope
      F.col("auth_method") =!= F.col("expected_auth_method")        // mismatch
    )

    // Return the alerts (retain all original columns plus the expected method)
    alerts.select(
      F.col("event_id"),
      F.col("timestamp"),
      F.col("account_id"),
      F.col("event_type"),
      F.col("source_host"),
      F.col("source_ip"),
      F.col("auth_method"),
      F.col("mfa_used"),
      F.col("session_id"),
      F.col("expected_auth_method")
    )
  }
}
