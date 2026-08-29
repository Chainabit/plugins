---
name: python
description: Maintainable Python practices for packages, environments, dependencies, typing, errors, configuration, and tests.
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "maintainable Python packages, environments, typing, and tests"
  layer: language
  requires: skill-software-engineering
---

# Python

Inspect the existing Python version, packaging metadata, environment manager, dependency lock files, and test runner. Keep importable packages cohesive, avoid hidden global state, type public boundaries where useful, and use explicit configuration and structured errors. Isolate environments and keep secrets out of source.

Prefer deterministic dependency declarations and reproducible commands already supported by the project. Test domain behavior and failure paths, not only framework wiring. Python-specific validation owns packaging, import, typing/configuration conventions; the foundation owns responsibilities and architecture.
