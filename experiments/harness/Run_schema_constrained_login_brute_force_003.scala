import org.apache.spark.sql.SparkSession


object Run_schema_constrained_login_brute_force_003 {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("Run_schema_constrained_login_brute_force_003").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath
    val events = spark.read.parquet(s"$repoRoot/data/processed/events")

    val alerts = BruteForceDetection.detect(events)
    println(s"\n===== schema_constrained / login-brute-force-003 (BruteForceDetection) =====")
    println(s"Total alerts raised: ${alerts.count()}")
    alerts.show(100, false)
    spark.stop()
  }
}
