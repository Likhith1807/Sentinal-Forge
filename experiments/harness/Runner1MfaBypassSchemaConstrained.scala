import org.apache.spark.sql.SparkSession

object Runner1MfaBypassSchemaConstrained {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("runner1").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath

    val events = spark.read.parquet(s"$repoRoot/data/processed/events")
    println(s"Total real events loaded: ${events.count()}")

    val alerts = ThreatDetection.detect(events)
    val rows = alerts.select("account_id", "event_id", "source_scenario").collect()

    println(s"\nschema_constrained mfa-bypass-003 (ThreatDetection) run against ALL real events:")
    println(s"Total alerts raised: ${rows.length}")
    rows.foreach(r => println(s"  ALERT account_id=${r.getString(0)} event_id=${r.getString(1)} source_scenario=${r.getString(2)}"))

    spark.stop()
  }
}
