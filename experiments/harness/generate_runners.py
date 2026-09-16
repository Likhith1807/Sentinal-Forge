"""Generates one small generic Scala runner per (system, behaviour) file,
so all 8 remaining held-out-generated baselines can be run for real
against the full Parquet replay store, not just read and reasoned about.
Each runner just prints every column of whatever detect() returns — no
need to know each file's exact output schema in advance, since they're
all heterogeneous by construction (see each baseline's README)."""
from pathlib import Path

TEMPLATE = """import org.apache.spark.sql.SparkSession
{import_line}

object {runner_name} {{
  def main(args: Array[String]): Unit = {{
    val spark = SparkSession.builder().appName("{runner_name}").master("local[*]").getOrCreate()
    spark.sparkContext.setLogLevel("WARN")
    val repoRoot = new java.io.File(".").getCanonicalPath
    val events = spark.read.parquet(s"$repoRoot/data/processed/events")

    val alerts = {call}
    println(s"\\n===== {system} / {behaviour} ({object_name}) =====")
    println(s"Total alerts raised: ${{alerts.count()}}")
    alerts.show(100, false)
    spark.stop()
  }}
}}
"""

MANIFEST = [
    # (system, behaviour, package, object_name, implicit_spark)
    ("direct_llm", "login-brute-force-003", None, "CredentialAccessDetector", False),
    ("direct_llm", "password-spray-003", None, "CredentialSprayingDetector", False),
    ("direct_llm", "concurrent-sessions-003", "com.sentinelforge.rules", "SimultaneousAccessRule", True),
    ("direct_llm", "mfa-bypass-003", None, "MFABypassDetection", False),
    ("schema_constrained", "login-brute-force-003", None, "BruteForceDetection", False),
    ("schema_constrained", "password-spray-003", None, "CredentialSprayingDetector", False),
    ("schema_constrained", "concurrent-sessions-003", None, "ThreatDetection", False),
    ("schema_constrained", "service-account-auth-003", None, "TokenOnlyServiceAccountDetection", False),
]

OUT_DIR = Path(__file__).parent

for system, behaviour, package, object_name, implicit_spark in MANIFEST:
    runner_name = f"Run_{system}_{behaviour}".replace("-", "_")
    import_line = f"import {package}.{object_name}" if package else ""
    call = f"{object_name}.detect(events)(spark)" if implicit_spark else f"{object_name}.detect(events)"
    content = TEMPLATE.format(
        import_line=import_line, runner_name=runner_name, call=call,
        system=system, behaviour=behaviour, object_name=object_name,
    )
    out_path = OUT_DIR / f"{runner_name}.scala"
    out_path.write_text(content, encoding="utf-8")
    print(f"Wrote {out_path}")
