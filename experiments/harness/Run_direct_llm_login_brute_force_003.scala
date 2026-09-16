import org.apache.spark.sql.SparkSession


object Run_direct_llm_login_brute_force_003 {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("Run_direct_llm_login_brute_force_003").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath
    val events = spark.read.parquet(s"$repoRoot/data/processed/events")

    val alerts = CredentialAccessDetector.detect(events)
    println(s"\n===== direct_llm / login-brute-force-003 (CredentialAccessDetector) =====")
    println(s"Total alerts raised: ${alerts.count()}")
    alerts.show(100, false)
    spark.stop()
  }
}
