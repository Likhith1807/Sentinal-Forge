import org.apache.spark.sql.SparkSession


object Run_direct_llm_mfa_bypass_003 {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("Run_direct_llm_mfa_bypass_003").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath
    val events = spark.read.parquet(s"$repoRoot/data/processed/events")

    val alerts = MFABypassDetection.detect(events)
    println(s"\n===== direct_llm / mfa-bypass-003 (MFABypassDetection) =====")
    println(s"Total alerts raised: ${alerts.count()}")
    alerts.show(100, false)
    spark.stop()
  }
}
