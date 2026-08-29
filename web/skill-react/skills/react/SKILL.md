---
name: react
description: React guidance for component responsibility, state, effects, dependency boundaries, testing, performance, and verified builds.
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "React components, state, boundaries, and verification"
  layer: framework
  requires: "skill-software-engineering, skill-project-bootstrap, skill-git, skill-project-documentation, skill-web-engineering"
---

# React

Inspect the existing React version, router, state/data libraries, build tool, package manager, and scripts. Keep components cohesive and presentational where possible; colocate local state with its owner and lift shared state only to the nearest stable owner. Keep effects for external synchronization, not derived values, and make data/loading/error boundaries explicit.

Use stable keys, accessible semantics, predictable memoization, and tests at behavior boundaries. Run actual project build/test/serve checks and inspect bundle/runtime behavior when relevant. React is a paved road, not a permission gate.
