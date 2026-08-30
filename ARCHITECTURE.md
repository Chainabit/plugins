# Plugin architecture

Chainabit plugins use a portable, versioned package format for distributing skills and related
capabilities. This document describes the public format and compatibility boundary. It does not
describe Chainabit's internal runtime, authorization implementation, registry operations, or
release-control topology.

## Package structure

```text
plugin/
├── chainabit-plugin.json
└── skills/
    └── <skill-name>/
        ├── SKILL.md
        ├── bundle.json
        ├── scripts/
        ├── references/
        └── assets/
```

`SKILL.md` is the portable Agent Skills boundary. The surrounding plugin manifest adds identity,
versioning, component references, compatibility, composition, permissions disclosure, and optional
installation metadata. Portable skill instructions should not depend on Chainabit-specific runtime
internals.

Only files accepted by the supported package contract are distributable. `scripts/` contains code
that may be executed in an isolated environment; `references/` contains supporting documentation;
`assets/` contains explicitly permitted, non-executable resources. The presence of a file does not
grant authority or make that file executable.

## Composition and capabilities

Plugins may declare dependencies on other compatible capabilities. Composition describes dependency
relationships only; it is not an authorization or technology allowlist. Required dependencies are
distinct from optional dependencies, and a capability declaration describes advertised behavior,
not proof that every host has the supporting runtime installed.

## Permissions and integrity

Plugins disclose requested authorities using the granular permission vocabulary. Declarations are
requests, not grants; the host decides what can run and under which constraints. Categories are
organizational labels only and are not security boundaries.

Marketplace listings may bind a package to an immutable source revision and package digest. Bundle
inventories independently record file hashes. These are integrity signals, not publisher identity,
publisher trust, or a security-review result. Signed attestations, when supported by a consuming
registry, are separate from all three.

## Compatibility

Marketplace validation checks manifests, component references, package structure, versions,
composition, permission disclosure, bundle inventories, and supported asset types before publication.
Schema evolution is additive where practical. Compatibility aliases preserve an old plugin ID only
when they are explicitly marked and contain no duplicate payload.

See [`spec/`](spec/) for machine-readable contracts, [`CONTRIBUTING.md`](CONTRIBUTING.md) for
authoring rules, and [`SECURITY.md`](SECURITY.md) for reporting and security expectations.
