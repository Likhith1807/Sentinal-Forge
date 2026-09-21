"""Scaled dataset construction for SENTINEL Forge (Phase B).

Modules:
    params        detection parameters, read from the compiled specs
    policy        deterministic synthetic account-policy assignment
    background    vectorised synthetic benign background events
    incidents     labelled B1-B5 incident injection
    refdetect     independent pure-Python reference detector (label cross-check)
    generate      command-line entry point that assembles a dataset
    lanl_mapper   maps LANL cyber1 auth events into the project schema

See docs/data-sources.md for what is real and what is synthetic.
"""

GENERATOR_VERSION = "1.0.0"
