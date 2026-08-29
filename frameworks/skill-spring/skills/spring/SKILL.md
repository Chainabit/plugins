---
name: spring
description: Spring guidance for module, controller, service, repository, configuration, security, testing, and verified commands.
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "Spring boundaries, configuration, security, and verification"
  layer: framework
  requires: "skill-java, skill-software-engineering, skill-project-bootstrap, skill-git, skill-project-documentation, skill-web-engineering"
---

# Spring

Inspect the actual Spring Boot/version, Maven or Gradle wrapper, module layout, configuration profiles, dependency injection graph, persistence, security, and existing test/build commands. Keep controllers/adapters thin, put use-case policy in cohesive services, and make repository boundaries explicit. Avoid service locators and broad mutable shared state.

Use typed configuration, environment-safe secrets, explicit transaction/security boundaries, and tests at domain plus HTTP/integration seams. Run the project’s real compile/test/package/run checks and document evidence. Spring adds framework decisions; Java and the foundations remain canonical.
