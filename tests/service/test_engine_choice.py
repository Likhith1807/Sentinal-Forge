"""`auto` engine selection is by cost, and explicit preferences are honoured."""
from __future__ import annotations

from sentinelforge.service import engines


def test_small_jsonl_uses_the_reference_engine_even_when_spark_is_available(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "spark_available", lambda: (True, ""))
    f = tmp_path / "e.jsonl"
    f.write_text('{"event_id": "1"}\n', encoding="utf-8")
    assert engines.choose("auto", f) == "reference"


def test_a_large_jsonl_uses_spark_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "spark_available", lambda: (True, ""))
    f = tmp_path / "e.jsonl"
    f.write_text("x" * 2000, encoding="utf-8")
    assert engines.choose("auto", f, reference_max_bytes=1000) == "spark"


def test_a_large_dataset_falls_back_to_the_reference_engine_without_a_jvm(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "spark_available", lambda: (False, "no java"))
    f = tmp_path / "e.jsonl"
    f.write_text("x" * 2000, encoding="utf-8")
    assert engines.choose("auto", f, reference_max_bytes=1000) == "reference"


def test_a_parquet_directory_is_never_sent_to_the_jsonl_only_reference_engine(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "spark_available", lambda: (True, ""))
    d = tmp_path / "events"
    d.mkdir()
    (d / "part-0.parquet").write_bytes(b"PAR1")
    assert engines.choose("auto", d) == "spark"


def test_explicit_preferences_win(tmp_path, monkeypatch):
    monkeypatch.setattr(engines, "spark_available", lambda: (True, ""))
    f = tmp_path / "e.jsonl"
    f.write_text("{}\n", encoding="utf-8")
    assert engines.choose("spark", f) == "spark"
    assert engines.choose("reference", f) == "reference"
