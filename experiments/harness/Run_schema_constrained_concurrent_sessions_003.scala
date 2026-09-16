import org.apache.spark.sql.SparkSession


object Run_schema_constrained_concurrent_sessions_003 {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("Run_schema_constrained_concurrent_sessions_003").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath
    val events = spark.read.parquet(s"$repoRoot/data/processed/events")

    val alerts = ThreatDetection.detect(events)
    println(s"\n===== schema_constrained / concurrent-sessions-003 (ThreatDetection) =====")
    println(s"Total alerts raised: ${alerts.count()}")
    alerts.show(100, false)
    spark.stop()
  }
}
