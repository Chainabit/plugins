---
name: git
description: "Project-aware Git hygiene: preserve history and compose ignore rules for secrets, dependencies, caches, runtime state, and generated output."
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "repository hygiene and composed ignore rules"
  layer: foundation
  requires: "skill-software-engineering, skill-project-bootstrap"
---

# Git capability

Inspect `.git`, remotes, branches, user configuration, and existing ignore rules before changing anything. If the deliverable is a maintainable software project, initialize or extend Git only when appropriate and preserve history. For a document, PDF, deck, spreadsheet, or other artifact-only output, repository setup is optional and must not obscure the artifact.

Compose ignore rules from common OS/editor noise, language, framework, build-tool, and project evidence. Normalize duplicates and preserve intentional rules; never overwrite blindly. Exclude secrets and environment credentials, dependencies, caches, build/coverage/test output, logs, sockets, PIDs, and editor state. Review `git status --ignored` and the final diff. Git owns the final hygiene decision and validates only repository cleanliness.
