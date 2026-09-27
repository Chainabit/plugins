---
name: project-bootstrap
description: Establishes an evidence-based project boundary, source/test/build layout, configuration, and clean temporary-output policy.
license: Apache-2.0
metadata:
  version: 1.1.0
  discovery: "project boundary and clean bootstrap"
  layer: foundation
  requires: skill-software-engineering
---

# Project bootstrap

Inspect first: current root, package/build manifests, source, tests, configuration, Git state, available runtimes, and generated material. Establish the project root that owns the deliverable; do not promote a parent workspace or sandbox.

Create only the structure justified by the project: source/modules, tests, configuration templates, build outputs outside source, and documentation. Keep secrets out of artifacts; use environment/config indirection. Put temporary files, caches, logs, coverage, sockets, PIDs, and generated previews in disposable locations. Follow framework conventions supplied by the selected technology skill instead of imposing a universal tree.

Exit when the boundary is understandable, commands are discoverable, generated output is separated, and Git/documentation integration has a clear owner. Validate bootstrap structure and cleanliness only; do not validate framework behavior here.

## Application contract

For application-capable workflows, describe the actual workspace through `chainabit.application-plan/v1` (see `references/application-plan.schema.json`). The contract identifies the inspected capability release, ordered prepare/build/test argv commands with relative working directories, a static output directory and entry path or a temporary runtime command/port/health path, and browser or HTTP verification paths. This is data, not a source-tree mandate. Inspect the selected technology handbook and the existing workspace; choose the structure that serves the user. Preserve the complete module, asset, stylesheet, font and route dependency graph.

A declared application capability is expertise, not permission. Use only the runtime operations and reviewed offline dependency closure admitted by the host. Unsupported dependencies must produce actionable evidence; do not bypass admission with external downloads or CDNs. Read stdout/stderr, status, timeouts and runtime/browser diagnostics from the existing tool loop; correct the source and retry within its budget. A build exit or HTTP 200 is insufficient evidence of a usable application.

Persistence, preview, packaging and appearance have separate owners. Keep source versions durable and processes disposable; do not create a file solely because the content uses a framework. Packages come from a coherent authorized source revision and omit secrets, caches and temporary runtime state. User/project/template branding overrides shared defaults; resolve the brand-default capability when branding is absent.
