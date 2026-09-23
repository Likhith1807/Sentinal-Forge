package sentinelforge.baselines.manual

/*
 * MANUAL-RULES BASELINE — hand-authored from
 * data/samples/reports/mfa-bypass-001.md. Same policy-join shape as
 * ServiceAccountInteractiveAuth.scala — see that file's header for why
 * `status` has three values instead of a boolean.
 */

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.functions._

object MfaBypassOnRequiredAccount {

  def detect(spark: SparkSession, events: DataFrame, policy: DataFrame): DataFrame = {
    import spark.implicits._

    events
      .filter($"event_type" === "login_success")
      .join(policy, Seq("account_id"), "left")
      .withColumn(
        "status",
        when($"mfa_required".isNull, lit("insufficient_context"))
          .when($"mfa_required" === true && $"mfa_used" === false, lit("alert"))
          .otherwise(lit("no_alert"))
      )
      .select(
        $"account_id",
        $"event_id",
        $"timestamp".as("detected_at"),
        $"mfa_used",
        $"mfa_required",
        $"status",
        lit("mfa-missing-on-required-account").as("behaviour_id")
      )
  }
}
