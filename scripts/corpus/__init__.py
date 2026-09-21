"""Synthetic threat-report corpus with exact, provenance-carrying gold (Phase B, step 6).

Every report is rendered from a structured fact sheet, so its gold labels are exact by
construction and every value carries a character-offset span. That also means the corpus is
synthetic and only as diverse as its renderers and LLM rewrites; docs/corpus.md states the
limits and what a human second annotation pass must still confirm.

Modules:
    vocab        field vocabulary, natural-language field phrases, names
    doc          span-tracking text builder
    facts        fact-sheet sampler (one per report family)
    render       template styles that turn a fact sheet into a report + spans
    unsupported  out-of-scope / unavailable-field report families (abstention cases)
    llm_rewrite  guarded LLM rewrite of a rendered report into a different style
    split        family-aware train/dev/test split and near-duplicate leakage check
    build        command-line entry point
    agreement    inter-annotator agreement for the human verification sample
"""

CORPUS_VERSION = "1.0.0"
