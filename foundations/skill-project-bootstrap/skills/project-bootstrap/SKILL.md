---
name: project-bootstrap
description: Establishes an evidence-based project boundary, source/test/build layout, configuration, and clean temporary-output policy.
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "project boundary and clean bootstrap"
  layer: foundation
  requires: skill-software-engineering
---

# Project bootstrap

Inspect first: current root, package/build manifests, source, tests, configuration, Git state, available runtimes, and generated material. Establish the project root that owns the deliverable; do not promote a parent workspace or sandbox.

Create only the structure justified by the project: source/modules, tests, configuration templates, build outputs outside source, and documentation. Keep secrets out of artifacts; use environment/config indirection. Put temporary files, caches, logs, coverage, sockets, PIDs, and generated previews in disposable locations. Follow framework conventions supplied by the selected technology skill instead of imposing a universal tree.

Exit when the boundary is understandable, commands are discoverable, generated output is separated, and Git/documentation integration has a clear owner. Validate bootstrap structure and cleanliness only; do not validate framework behavior here.
