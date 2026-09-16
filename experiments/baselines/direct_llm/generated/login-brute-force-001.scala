import org.apache.spark.sql.{DataFrame, SparkSession, Column}
import org.apache.spark.sql.functions._
import org.apache.spark.sql.expressions.Window

object CredentialBruteForceDetector {

  /** Detects a pattern of 5+ failed logins within 2 minutes followed by a
    * successful login (without MFA) for the same account and source host.
    *
    * @param events authentication log DataFrame following the documented schema
    * @return DataFrame containing alert rows
    */
  def detect(events: DataFrame): DataFrame = {
    // --------------------------------------------------------------------
    // 1. Normalise timestamp and keep only required columns
    // --------------------------------------------------------------------
    val df = events
      .withColumn("event_ts", to_timestamp(col("timestamp")))
      .select(
        col("event_id"),
        col("event_ts"),
        col("account_id"),
        col("event_type"),
        col("source_host"),
        col("source_ip"),          // optional, kept for possible future use
        col("auth_method"),
        col("mfa_used"),
        col("session_id")
      )

    // --------------------------------------------------------------------
    // 2. Create a monotonically increasing group id that increments on each success.
    //    All failures that occur before the next success share the same group_id.
    // --------------------------------------------------------------------
    val wOrder = Window.partitionBy(col("account_id")).orderBy(col("event_ts"))
    val groupIdCol = sum(when(col("event_type") === "login_success", 1).otherwise(0))
      .over(wOrder)
      .alias("group_id")

    val dfWithGroup = df.withColumn("group_id", groupIdCol)

    // --------------------------------------------------------------------
    // 3. Aggregate failures per (account_id, group
