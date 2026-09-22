ThisBuild / scalaVersion := "2.13.12"
ThisBuild / version      := "0.1.0"

// Spark 3.5.x: last line still on Scala 2.12/2.13 (not 2.13-only, not
// Scala 3) — matches the README's "Scala + Apache Spark" scope, and is why
// Phase 4 targets 2.13 instead of the Scala 3 coursier's default `scalac`
// alias resolved to (see docs/spec/stage4-scala-toolchain.md).
val sparkVersion = "3.5.3"

// Shared between `Compile / run` and `Test`: ScalaTest specs that touch Spark (RuleCompilerSpec)
// hit the exact same DirectBuffer reflection block `sbt run` needs these flags for, and
// `Test / javaOptions` is a separate scope from `Compile / run / javaOptions` — without this,
// `sbt test` fails with the same UnsatisfiedLinkError `stage4-scala-toolchain.md` documents for
// `sbt run`.
val sparkAddOpens = Seq(
  "--add-opens=java.base/java.lang=ALL-UNNAMED",
  "--add-opens=java.base/java.lang.invoke=ALL-UNNAMED",
  "--add-opens=java.base/java.lang.reflect=ALL-UNNAMED",
  "--add-opens=java.base/java.io=ALL-UNNAMED",
  "--add-opens=java.base/java.net=ALL-UNNAMED",
  "--add-opens=java.base/java.nio=ALL-UNNAMED",
  "--add-opens=java.base/java.util=ALL-UNNAMED",
  "--add-opens=java.base/java.util.concurrent=ALL-UNNAMED",
  "--add-opens=java.base/java.util.concurrent.atomic=ALL-UNNAMED",
  "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED",
  "--add-opens=java.base/sun.nio.cs=ALL-UNNAMED",
  "--add-opens=java.base/sun.security.action=ALL-UNNAMED",
  "--add-opens=java.base/sun.util.calendar=ALL-UNNAMED",
  "-Djdk.reflect.useDirectMethodHandle=false",
)

lazy val root = (project in file("."))
  .settings(
    name := "sentinel-forge",
    libraryDependencies ++= Seq(
      "org.apache.spark" %% "spark-sql" % sparkVersion,
      "org.scalatest"    %% "scalatest" % "3.2.19" % Test,
    ),
    // The project's Scala sources live where each phase put them
    // (compiler/test/golden for the Phase 0 golden reference,
    // experiments/baselines/manual for the Phase 1 manual baseline), not
    // in a src/main/scala layout — pointing sbt at those directories
    // directly avoids moving files that other docs already link to by path.
    Compile / unmanagedSourceDirectories ++= Seq(
      baseDirectory.value / "compiler" / "test" / "golden",
      baseDirectory.value / "experiments" / "baselines" / "manual",
      baseDirectory.value / "compiler" / "src" / "main" / "scala",
    ),
    // ScalaTest specs, same reasoning as above: the project root isn't `compiler/`, so sbt's
    // default `src/test/scala` never gets found without registering the real path explicitly.
    Test / unmanagedSourceDirectories ++= Seq(
      baseDirectory.value / "compiler" / "src" / "test" / "scala",
    ),
    // Spark 3.5's storage layer reflectively touches sun.nio.ch.DirectBuffer,
    // which Java 9+'s module system blocks by default — these are the same
    // --add-opens flags Spark's own spark-submit script sets automatically;
    // running via `sbt run` needs them supplied explicitly instead.
    fork := true,
    // Opt-in heap for large local runs: sbt -Dsf.heap=10g "runMain ..."
    Compile / run / javaOptions ++= sys.props.get("sf.heap").map(h => s"-Xmx$h").toSeq,
    Compile / run / javaOptions ++= sparkAddOpens,
    Test / javaOptions ++= sys.props.get("sf.heap").map(h => s"-Xmx$h").toSeq,
    Test / javaOptions ++= sparkAddOpens,
  )
