"""Real test run for nlp/src/injection_guard.py against the 3 adversarial
fixtures in compiler/test/fixtures/adversarial/. See injection_guard.py's
module docstring for why this exists.

Usage:
    python nlp/test_injection_guard.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))
import injection_guard as ig  # noqa: E402

REPO_ROOT = Path(__file__).parent.parent
FIXTURES_DIR = REPO_ROOT / "compiler" / "test" / "fixtures" / "adversarial"

EXPECTED = {
    "injection-001-ignore-instructions.md": True,
    "injection-002-fake-policy-override.md": True,
    "injection-003-benign-report-no-injection.md": False,
}


def main() -> None:
    failures = 0
    for filename, expected_flagged in EXPECTED.items():
        text = (FIXTURES_DIR / filename).read_text(encoding="utf-8")
        result = ig.scan(text)
        ok = result.flagged == expected_flagged
        marker = "OK " if ok else "FAIL"
        print(f"{marker} {filename}: flagged={result.flagged} (expected {expected_flagged}), {len(result.matches)} match(es)")
        if not ok:
            failures += 1

    if failures:
        raise SystemExit(f"{failures} fixture(s) did not match expected guard behaviour")
    print("\nAll 3 adversarial fixtures behaved as expected.")


if __name__ == "__main__":
    main()
