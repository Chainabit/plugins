---
name: java
description: Maintainable Java practices for package/module boundaries, types, dependencies, configuration, errors, and tests.
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "maintainable Java packages, builds, types, and tests"
  layer: language
  requires: skill-software-engineering
---

# Java

Inspect the actual JDK, Maven/Gradle wrapper, source layout, dependency lock/configuration, and test commands. Keep packages cohesive and APIs explicit; prefer immutable value types where appropriate, narrow interfaces, explicit lifecycle ownership, and meaningful exception boundaries. Do not hide configuration or credentials in source.

Use the project’s wrapper and existing dependency policy, avoid unneeded installations, and verify compile, test, and run paths. Java-specific validation owns language/build conventions; generic architecture remains in software-engineering.
