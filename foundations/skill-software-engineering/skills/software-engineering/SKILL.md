---
name: software-engineering
description: "Technology-neutral methodology for maintainable software: responsibility ownership, architecture, testing, and evidence-driven fixes."
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "engineering invariants and evidence-driven implementation"
  layer: foundation
---

# Software engineering foundation

Use this as methodology, not as a code generator. Chao chooses strategy; Chainabit Computer inspects and executes; security controls authority. A missing technology skill never forbids an otherwise available and authorized toolchain.

## Invariants

- Assign each behavior to an explicit owner. Apply GRASP Information Expert, Controller, Creator, Low Coupling, High Cohesion, Protected Variations, and dependency inversion.
- Separate domain policy, orchestration, adapters, configuration, and delivery concerns. Make lifecycle and state ownership explicit.
- Prefer small interfaces and stable seams. Design for testability, retries, and idempotency where operations can repeat.
- Inspect the real workspace, commands, dependencies, and failure evidence before deciding. Trace failures to root causes and fix the responsible boundary rather than adding a case-specific patch.

## Workflow and exit criteria

Inspect → model responsibilities and variations → implement the smallest coherent change → run real build/run/test checks → review the diff and workspace. Finish only when behavior is evidenced, failure paths are considered, and generated/runtime material is separated from source.

Validate only these invariants here. Technology, artifact, repository, and web validators own their specialized checks.
