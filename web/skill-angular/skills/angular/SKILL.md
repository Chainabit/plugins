---
name: angular
description: Angular guidance for component responsibility, state, dependency boundaries, testing, performance, and verified builds.
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "Angular components, state, boundaries, and verification"
  layer: framework
  requires: "skill-software-engineering, skill-project-bootstrap, skill-git, skill-project-documentation, skill-web-engineering"
---

# Angular

Inspect the existing Angular version, workspace configuration, package manager, standalone/module style, routing, and scripts. Keep components focused on presentation and local interaction; place shared state and domain policy behind explicit services/facades with clear ownership. Keep dependency injection boundaries narrow and avoid circular feature coupling.

Use typed inputs/outputs, accessible templates, sensible change detection and lazy boundaries, and tests at component/service seams. Run the project’s real lint/build/test/serve commands and document only verified commands. Angular guidance supplements the foundations and never forbids another authorized tool.
