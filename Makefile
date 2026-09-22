# SENTINEL Forge — common tasks. Portable (plain python/sbt/docker invocations underneath,
# nothing make-specific); on Windows without `make` installed, run the commands below directly.

.PHONY: demo test test-python test-scala docker-up docker-down

## Fast, offline walkthrough of the report-to-detection path (seconds, no API key, no JVM/Spark).
demo:
	python scripts/demo.py

## Full test suite: pytest (Python) + sbt test (Scala/Spark).
test: test-python test-scala

test-python:
	pytest -v

test-scala:
	sbt test

## Run the analyst dashboard locally in Docker (see docker-compose.yml).
docker-up:
	docker compose up --build

docker-down:
	docker compose down
