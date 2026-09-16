import org.apache.spark.sql.SparkSession
import com.sentinelforge.detections.TokenAuthMismatchDetector

object Runner2ServiceAccountDirectLlm {
  def main(args: Array[String]): Unit = {
    val spark = SparkSession.builder().appName("runner2").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath

    val events = spark.read.parquet(s"$repoRoot/data/processed/events")

    val alerts = TokenAuthMismatchDetector.detect(events)
    val rows = alerts.select("account_id", "event_id", "source_ip").collect()

    println(s"\ndirect_llm service-account-auth-003 (TokenAuthMismatchDetector) run against ALL real events:")
    println(s"Total alerts raised: ${rows.length}")
    rows.foreach(r => println(s"  ALERT account_id=${r.get(0)} event_id=${r.get(1)}"))
    println(s"\nExpected true positive: account_id=svc-etl05 event_id=evt-sa01 (SA-POS scenario)")
    println(s"svc-etl05 detected: ${rows.exists(_.getString(0) == "svc-etl05")}")

    spark.stop()
  }
}
