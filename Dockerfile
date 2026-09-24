# SENTINEL Forge - two build targets from one file.
#
#   docker build --target slim -t sentinel-forge:slim .    ~150 MB. The analyst UI/API with the Python reference
#                                                          engine (no JVM). Every result says which engine ran it.
#   docker build --target full -t sentinel-forge:full .    JDK 17 + sbt + the compiled Spark executors: real Spark
#                                                          batch and streaming runs.
#
# Neither image contains the fine-tuned checkpoint (500 MB, trained separately - docs/evaluation.md) or torch:
# the UI then offers "Evidence check only" and says why the fine-tuned extractor is unavailable.
#
# Access: a container is reached from outside loopback, so the API is TOKEN-protected. Set SF_API_TOKENS
# ("name:token:role,...") or let it generate one - it is printed once in the container log at start-up.

FROM python:3.11-slim AS slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 SF_STATE_DIR=/data SF_ENGINE=auto SF_REQUIRE_AUTH=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY sentinelforge ./sentinelforge
COPY dashboard ./dashboard
COPY compiler/src ./compiler/src
COPY nlp/src/schema_fields.py nlp/src/classical_extractor.py nlp/src/injection_guard.py ./nlp/src/
COPY data/demo ./data/demo
COPY data/samples/schema ./data/samples/schema
COPY experiments/results ./experiments/results
RUN useradd --create-home --uid 10001 forge && mkdir /data && chown forge /data
USER forge
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "uvicorn", "dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ----------------------------------------------------------------------------------------------------------------
FROM eclipse-temurin:17-jdk-jammy AS full
ARG SBT_VERSION=1.10.5
ENV DEBIAN_FRONTEND=noninteractive PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    SF_STATE_DIR=/data SF_ENGINE=auto SF_REQUIRE_AUTH=1 SF_JAVA=/opt/java/openjdk/bin/java \
    COURSIER_CACHE=/opt/coursier
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip python3-venv curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL "https://github.com/sbt/sbt/releases/download/v${SBT_VERSION}/sbt-${SBT_VERSION}.tgz" | tar -xz -C /opt \
    && ln -s /opt/sbt/bin/sbt /usr/local/bin/sbt
WORKDIR /app
COPY requirements.txt .
RUN python3 -m pip install -r requirements.txt
# dependency resolution first (cached until build.sbt changes), then sources
COPY build.sbt ./
COPY project/build.properties ./project/build.properties
RUN sbt -batch update
COPY compiler ./compiler
# build.sbt also compiles the hand-written baseline rules (the comparison the evaluation is measured against)
COPY experiments/baselines/manual ./experiments/baselines/manual
COPY sentinelforge ./sentinelforge
COPY dashboard ./dashboard
COPY nlp/src/schema_fields.py nlp/src/classical_extractor.py nlp/src/injection_guard.py ./nlp/src/
COPY data/demo ./data/demo
COPY data/samples/schema ./data/samples/schema
COPY experiments/results ./experiments/results
# the classpath points into the dependency cache; the app runs as a non-root user, so the cache must be readable
RUN python3 -m sentinelforge.spark_engine --force && chmod -R a+rX /opt/coursier /app/target
RUN useradd --create-home --uid 10001 forge && mkdir /data && chown -R forge /data /app/target /app/project
USER forge
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python3 -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"
CMD ["python3", "-m", "uvicorn", "dashboard.backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
