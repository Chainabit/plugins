---
name: project-documentation
description: "Evidence-based README completion: purpose, setup, configuration, structure, and verified project commands."
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "evidence-based README and command verification"
  layer: foundation
  requires: "skill-software-engineering, skill-project-bootstrap"
---

# Project documentation

Inspect the completed project, manifest files, configuration, entry points, tests, and actual successful runs. README content normally covers purpose, architecture when it helps a maintainer, prerequisites, installation, configuration/environment requirements, real run/build/test commands, and relevant structure. Use placeholders for secrets and say when a capability is optional.

Never invent commands because they are conventional for a language or framework. Run each documented command (or record why it cannot run) and keep documentation consistent with the artifact. Validate documentation against project evidence; documentation owns README accuracy, not general architecture or Git policy.
