package sentinelforge.baselines.manual

/*
 * MANUAL-RULES BASELINE — hand-authored from
 * data/samples/reports/service-account-auth-001.md.
 *
 * This one needs a second data source: account_policy_reference.json
 * (see docs/schema/account-policy-reference.md). It is included here
 * specifically to test whether a manual/hand-authored rule handles a
 * missing policy entry responsibly (status = insufficient_context) rather
 * than defaulting an unknown account to "compliant" — see
 * data/samples/replay/service_account_auth_labels.json, scenario
 * SA-NEG-NO-POLICY.
 */

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

object ServiceAccountInteractiveAuth {

  /** @param events  authentication_log_schema.json v1
    * @param policy  account_policy_reference.json `records`, as a DataFrame
    *                with columns account_id, account_type,
    *                expected_auth_method, mfa_required
    */
  def detect(spark: SparkSession, events: DataFrame, policy: DataFrame): DataFrame = {
    import spark.implicits._

    events
      .filter($"event_type" === "login_success")
      .join(policy, Seq("account_id"), "left")
      .withColumn(
        "status",
        when($"expected_auth_method".isNull, lit("insufficient_context"))
          .when($"auth_method" =!= $"expected_auth_method", lit("alert"))
          .otherwise(lit("no_alert"))
      )
      .select(
        $"account_id",
        $"event_id",
        $"timestamp".as("detected_at"),
        $"auth_method",
        $"expected_auth_method",
        $"status",
        lit("service-account-interactive-auth").as("behaviour_id")
      )
  }
}
