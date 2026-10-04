# Chainabit plugin contracts

These files are the public, portable boundary for plugin authors. A `Skill` is the
Agent Skills-compatible `skills/<name>/SKILL.md` payload. A `Plugin` is the Chainabit
manifest that packages one or more skills and runtime components. A `Marketplace Listing`
resolves an ID to a plugin source; it is not telemetry or a trust decision.

The JSON Schemas describe shape. `tooling/validate-marketplace.mjs` is the executable
semantic contract for rules involving the filesystem, dependency graph, bundle hashes,
immutable source identity, permission disclosure, and safe assets.

The published shapes are [`plugin-manifest.schema.json`](plugin-manifest.schema.json),
[`marketplace.schema.json`](marketplace.schema.json), [`skill-bundle.schema.json`](skill-bundle.schema.json),
[`permissions.schema.json`](permissions.schema.json), [`capabilities.schema.json`](capabilities.schema.json),
[`composition.schema.json`](composition.schema.json), [`installation.schema.json`](installation.schema.json),
[`integrity.schema.json`](integrity.schema.json), and the future-facing
[`attestation.schema.json`](attestation.schema.json). Schemas do not replace the semantic
validator: they cannot inspect a repository, resolve a graph, or prove that bytes match a digest.

## Contract responsibilities

| Contract | Public responsibility | Important invariant |
| --- | --- | --- |
| `chainabit-plugin.json` | Plugin declaration | metadata and declarations do not grant authority |
| `marketplace.json` | Published catalog entry | one ID resolves to one source, version, and digest |
| `bundle.json` | Portable bundle inventory | every fetched byte is inventory-listed and hashed |
| `composition.requires` | Dependency declaration | dependency relationships are deterministic and acyclic |
| `capabilities` | Capability declaration | requested and advertised behavior stay distinct |
| `permissions.requested` | Permission disclosure | requested authority is not granted authority |
| `installation.packages` | Structured install metadata | exact package identity; no shell interpolation |
| registry/trust data | External operational services | mutable telemetry and trust are not authored here |

## Version and source rules

Schema versions are additive. Version 1 file-form skills remain readable; version 2 adds
directory bundles and the fields documented here. Marketplace listings use `revision` as an
immutable Git commit identity and `integrity.packageSha256` as a content identity. The digest
is calculated over a sorted, canonical file inventory for the plugin, excluding generated
`bundle.json` files; bundle entries independently hash every portable file.

`composition.requires` currently accepts an ID string for exact current-marketplace behavior
or `{ "id": "...", "version": "^1.0.0" }` for forward-compatible version constraints.
Resolution is deterministic and rejects missing, conflicting, or cyclic dependencies.
The current resolver supports exact versions, caret ranges, and tilde ranges. A future
multi-marketplace resolver must add explicit source/priority and lock-state rules before
allowing multiple versions to coexist; it must not select by network order or index order.

## Bundle assets

Directory-form skills may use `assets/` for inert resources such as PNG/JPEG/WebP images, SVG
without script/event/remote content, WOFF/WOFF2 fonts, CSS without remote imports, JSON, CSV, and
text. Asset bytes are checked against their declared type and are never executable. Each bundle is
limited to 64 files, 512 KiB per file, and 4 MiB total; PNG dimensions are limited to 8192 pixels
per axis. Symlinks, unsupported extensions, malformed signatures, and remote asset references are
rejected. These limits are package-contract limits, not a claim that every consuming runtime uses
the same storage or rendering limits.

## Artifact byte and execution ownership

An artifact generator reports the output path, byte length, content hash and
format of the file it actually wrote and reopened. A generator does not claim
that the artifact has been promoted. The consuming host owns execution placement,
validation, persistence and cleanup; a plugin must not independently select a
different workspace or create promotion records.

A declared validator inspects the supplied target without modifying it. Its
successful subject identifies the supplied relative path selector (independent
of the sandbox mount), shape, byte length and SHA-256 of the
same captured bytes that were parsed. Tree validators use the complete selected
file set, including validation metadata, with paths sorted by their UTF-8 bytes.
Each tree identity record is `path`, NUL, the file's SHA-256, NUL, its decimal
byte length, and a newline. File validators hash the file bytes directly.
Reopening a path to compute identity after parsing is unsafe: a concurrent
producer may replace the file between those operations.

The host binds that subject to its captured output before promotion. A missing
target, changed bytes, unavailable execution context or validator rejection
must stop promotion with a structured failure. Successful process exit alone
does not establish byte identity. Validator tests must include replacement of
the producer's path after capture and verify that parsing and reported identity
still refer to the same bytes.

## Trust model

File integrity, package integrity, publisher identity, publisher trust, security review, and
registry trust are separate facts. This repository does not expose `verified`, `downloads`,
`lastChecked`, `stale`, or self-authored signature claims. A future signed attestation is an
externally owned envelope over the canonical package digest; an attestation can prove who signed
bytes but cannot by itself establish that the publisher is trusted or that a security review passed.

Portable skill content remains separate from Chainabit-specific orchestration. Consumers may adapt
the package to their own runtime while preserving the `skills/<skill-name>/SKILL.md` boundary.
