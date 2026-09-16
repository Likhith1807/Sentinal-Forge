package sentinelforge.compiler.golden

/*
 * GOLDEN REFERENCE — Phase 0 canonical example.
 *
 * This is the target output the Stage-4 compiler must eventually produce
 * from data/samples/ir/login-brute-force-001.json, validated against
 * data/samples/schema/authentication_log_schema.json.
 *
 * It is hand-written, not compiler-generated: there is no build defined for
 * `compiler/` yet (that lands in Phase 4). It exists now so that (a) Phase 4
 * has a concrete target to compile *to*, and (b) the replay harness in
 * Phase 5 has a real rule to run against data/samples/replay/ before any
 * other component exists. When the real compiler is built, its output for
 * the same IR should be structurally equivalent to this file.
 */

import org.apache.spark.sql.{DataFrame, SparkSession}
import org.apache.spark.sql.expressions.Window
import org.apache.spark.sql.functions._

/** Compiled from behaviourId = "repeated-failed-login-then-success"
  * (source: data/samples/ir/login-brute-force-001.json)
  *
  *   - groupingKey: account_id
  *   - timeWindow:  2 minutes, sliding
  *   - sequence:    >= 5 login_failure, immediately followed by 1 login_success
  *
  * Every field this rule reads (account_id, event_type, timestamp) is
  * `required` in authentication_log_schema.json v1 — this is what made
  * Stage 3 mark the source IR "supported" rather than rejecting it.
  * `source_ip` is deliberately never referenced, per the IR's
  * `excludedFields` entry.
  */
object LoginBruteForceThenSuccess {

  final val BehaviourId  = "repeated-failed-login-then-success"
  final val FailureCount = 5
  final val WindowMinutes = 2

  /** @param events must conform to authentication_log_schema.json v1:
    *   event_id, timestamp, account_id, event_type, source_host,
    *   auth_method, mfa_used [, source_ip, session_id]
    */
  def detect(spark: SparkSession, events: DataFrame): DataFrame = {
    import spark.implicits._

    // NOTE (added after Phase 4/5 real execution): $"timestamp".cast("long")
    // was the original line here. It silently casts the raw ISO-8601
    // STRING straight to long instead of parsing it — Spark returns null
    // for every row rather than an error, which breaks the window's ORDER
    // BY and made rangeBetween effectively unbounded. This was never
    // caught across Phases 0-3 because this file was hand-written text,
    // never actually run, until compiler/src/main/scala's real Spark
    // execution surfaced it by failing scenario E in
    // data/samples/replay/login_brute_force_labels.json. Left documented
    // here rather than silently fixed — see
    // docs/spec/stage4-scala-toolchain.md and
    // experiments/results/phase5_manual_baseline_check.json.
    val parsedTs = to_timestamp($"timestamp", "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'").cast("long")

    val byAccountTime = Window
      .partitionBy($"account_id")
      .orderBy(parsedTs)
      .rangeBetween(-WindowMinutes * 60L, 0L)

    val withRollingFailures = events
      .withColumn("ts_long", parsedTs)
      .withColumn(
        "recent_failures",
        sum(when($"event_type" === "login_failure", 1).otherwise(0))
          .over(byAccountTime)
      )

    withRollingFailures
      .filter($"event_type" === "login_success")
      .filter($"recent_failures" >= FailureCount)
      .select(
        $"account_id",
        $"event_id".as("triggering_success_event_id"),
        $"timestamp".as("detected_at"),
        $"recent_failures",
        lit(BehaviourId).as("behaviour_id")
      )
  }
}
