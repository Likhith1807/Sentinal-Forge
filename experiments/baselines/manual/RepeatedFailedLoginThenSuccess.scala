package sentinelforge.baselines.manual

/*
 * MANUAL-RULES BASELINE for B1. Deliberately NOT a fresh reimplementation:
 * it delegates straight to the Phase 0 golden reference
 * (compiler.golden.LoginBruteForceThenSuccess). That's not a shortcut, it's
 * the honest result — B1 is simple enough (one account, one threshold, one
 * window, no second data source) that a careful analyst and a correct
 * compiler produce the same logic. Any daylight between "manual" and
 * "SENTINEL Forge" in the Phase 5 results table shows up on B2–B5, where
 * the grouping keys, cross-entity correlation, and policy-reference join
 * give a hand-written rule real chances to under- or over-specify.
 */

import org.apache.spark.sql.{DataFrame, SparkSession}
import sentinelforge.compiler.golden.LoginBruteForceThenSuccess

object RepeatedFailedLoginThenSuccess {
  def detect(spark: SparkSession, events: DataFrame): DataFrame =
    LoginBruteForceThenSuccess.detect(spark, events)
}
