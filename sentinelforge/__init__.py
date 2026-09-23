"""SENTINEL Forge core: evidence-backed report-to-detection compilation.

Layers, each importable on its own and free of network / GPU / JVM requirements:

* :mod:`sentinelforge.behaviours`  - the single registry of supported detections
* :mod:`sentinelforge.conditions`  - deterministic, evidence-carrying condition finder
* :mod:`sentinelforge.reconcile`   - cross-checks any extractor's proposal against that evidence
* :mod:`sentinelforge.validation`  - strict spec + dataset-schema validation
* :mod:`sentinelforge.compile`     - verified specification -> compiled rule (with a content-hash version)
* :mod:`sentinelforge.refengine`   - independent pure-Python executor of a compiled rule
"""
__version__ = "1.0.0"
