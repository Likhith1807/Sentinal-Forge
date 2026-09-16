import org.apache.spark.sql.SparkSession
import com.sentinelforge.rules.SimultaneousAccessRule

object Run_direct_llm_concurrent_sessions_003 {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("Run_direct_llm_concurrent_sessions_003").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath
    val events = spark.read.parquet(s"$repoRoot/data/processed/events")

    val alerts = SimultaneousAccessRule.detect(events)(spark)
    println(s"\n===== direct_llm / concurrent-sessions-003 (SimultaneousAccessRule) =====")
    println(s"Total alerts raised: ${alerts.count()}")
    alerts.show(100, false)
    spark.stop()
  }
}
