import org.apache.spark.sql.SparkSession


object Run_schema_constrained_service_account_auth_003 {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("Run_schema_constrained_service_account_auth_003").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath
    val events = spark.read.parquet(s"$repoRoot/data/processed/events")

    val alerts = TokenOnlyServiceAccountDetection.detect(events)
    println(s"\n===== schema_constrained / service-account-auth-003 (TokenOnlyServiceAccountDetection) =====")
    println(s"Total alerts raised: ${alerts.count()}")
    alerts.show(100, false)
    spark.stop()
  }
}
