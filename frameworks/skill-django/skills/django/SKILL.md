---
name: django
description: Django guidance for app boundaries, models, views, services, configuration, migrations, security, tests, and commands.
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "Django boundaries, configuration, migrations, and tests"
  layer: framework
  requires: "skill-python, skill-software-engineering, skill-project-bootstrap, skill-git, skill-project-documentation, skill-web-engineering"
---

# Django

Inspect Django/Python versions, settings split, URL routing, installed apps, models, migrations, templates/API layer, task/runtime services, and actual manage.py commands. Keep apps cohesive; let models own invariants they can know, keep orchestration in explicit services/use cases when it crosses models, and keep views/controllers thin.

Treat migrations as reviewed source, configuration as environment-specific, and authentication/CSRF/permissions as explicit security boundaries. Test domain behavior, request contracts, migrations, and failure paths. Verify the project’s real check, test, run, and build/static commands; do not invent them.
