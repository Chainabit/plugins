# Chainabit Plugins — Composable Capability Marketplace

A git-based reference marketplace for the Chainabit plugin format — a cross-platform, versioned
bundle format installable from the NEXUS desktop app, the Chainabit CLI, or the web app. This
repo exists to prove the loop end-to-end: `marketplace add` → `install <id>` with zero backend.

The public contracts, schemas, semantic fixtures, trust model, and authoring guidance live in
[`spec/`](spec/) and this repository's [security policy](SECURITY.md). They are intentionally
usable without access to a private Chainabit repository.

## What's here

Skills are capability layers, not a flat prompt catalog. A small discovery description selects
the relevant plugin; `composition.requires` resolves methodology dependencies in dependency-first
order; only the selected core skill is loaded. Detailed references and deterministic validators
remain opt-in. Composition is guidance only: it grants no authority and is never a technology
allowlist. Chainabit Computer may use any authorized, available technology.

The marketplace is physically organized by responsibility: foundations, languages, frameworks,
web, infrastructure, databases, cloud, devops, testing, security, data, ai, tooling, artifacts,
providers, and personas. The category folders are discovery and maintenance boundaries, while
`chainabit-plugin.json` remains the identity and install contract. The engineering expansion adds
100 focused language, framework, platform, architecture, delivery, quality, security, data, and AI
capabilities without changing existing public identifiers.

| Plugin | Category | What it illustrates |
|--------|----------|----------------------|
| [`providers/provider-acmegate/`](providers/provider-acmegate/) | `providers`, `security` | An illustrative provider component with explicit host-process and network authority declarations. **Illustrative only** — AcmeGate is a fictional example product, not a real integration. |
| [`personas/persona-reviewer/`](personas/persona-reviewer/) | `personas`, `code` | An installable agent persona + a slash command, with zero permissions requested. |
| [`artifacts/skill-pdf/`](artifacts/skill-pdf/) | `productivity`, `data` | A `schemaVersion` 2 executable skill: `components.skills` points at a *directory* holding `SKILL.md` plus `scripts/`, `references/`, and optional non-executable `assets/`. Generates real PDFs; the scripts run in a sandbox, never on the host. |
| [`artifacts/skill-xlsx/`](artifacts/skill-xlsx/) | `productivity`, `data` | The same shape applied to spreadsheets. Illustrates a skill whose value is a strict input spec: typed columns in, a real `.xlsx` out, and a validator that catches numbers silently stored as text. |
| [`artifacts/skill-pptx/`](artifacts/skill-pptx/) | `productivity`, `data` | The same shape applied to presentations. Illustrates a skill whose value is refusal: fixed layouts and a checked palette instead of styling knobs, and a validator that fails a deck for empty slides, overflowing text, low contrast, or too many bullets. |
| [`artifacts/skill-website/`](artifacts/skill-website/) | `productivity`, `code` | A compatibility alias that preserves the historical `skill-website` identity while delegating to the canonical static website capability. |
| `skill-software-engineering`, `skill-project-bootstrap`, `skill-git`, `skill-project-documentation` | `code` | Reusable engineering foundations for responsibility ownership, clean project boundaries, repository hygiene, and evidence-based README completion. |
| `skill-web-engineering`, `skill-static-website` | `code` | Shared web quality methodology plus the static HTML/CSS implementation variation. |
| `skill-python`, `skill-java`, `skill-angular`, `skill-react`, `skill-django`, `skill-spring` | `code` | Focused language/framework paved roads composed from the foundations; they are not execution allowlists. |
| [`providers/provider-solongate/`](providers/provider-solongate/) | `providers`, `security` | A real partner integration: SolonGate, a security proxy / MCP gateway. Unlike the examples above, this is a real, installable product (`@solongate/proxy` on npm). |
| [`providers/provider-codegraph/`](providers/provider-codegraph/) | `code`, `providers` | A real partner integration: CodeGraph, local-first code intelligence over an MCP server. Declares `workspaceAccess`, so it also illustrates how a plugin discloses reading the user's open project. |

`marketplace.json` at the repo root lists the installable set by id, version, immutable revision,
and package digest, so any Chainabit surface can resolve and install them reproducibly from this repo.

Each listing's `source` is this repo plus a `#<category>/<plugin-id>` fragment naming the
manifest's repository-relative root. `revision` pins the source to an immutable Git commit and
`integrity.packageSha256` binds the portable package content. A client must verify both before
materializing a plugin. The index contains authored catalog data only; downloads, freshness,
publisher verification, security review, and signature status belong to external registry/trust
services and are never contributor-controlled booleans here.

## Try it

Install from a Chainabit-compatible client or marketplace consumer:

```
chainabit nexus marketplace add https://github.com/chainabit/plugins
chainabit nexus install skill-pdf
```

## Validating

Run before pushing any change to a manifest or to `marketplace.json`:

```
node tooling/validate-marketplace.mjs
node tooling/validate-marketplace.mjs --write-bundles
node tooling/resolve-skills.mjs skill-django
```

No dependencies, no install step. It recursively discovers category-contained manifests and checks each against the public
contract, confirms each declared component file exists, and cross-checks `marketplace.json`
against the folders on disk (same ids, same versions, each `source` fragment naming its own
folder). Exits non-zero on the first problem, so it can be used in local checks or CI.

The validator is the executable public contract for rules JSON Schema cannot fully express:

- A manifest declaring an executing component or lifecycle package **must** declare the matching
  granular requested authority.
- A skill directory containing `scripts/` **must** declare `permissions.execute`, so the
  install consent sheet discloses that the plugin ships code meant to be run.
- A skill directory's name **must** equal the `name` in its `SKILL.md` frontmatter. They are two
  declarations of one identity, and nothing else catches them drifting apart.

Pull requests and protected branch updates run this validator automatically in
`.github/workflows/marketplace.yml`; an invalid state is not mergeable through the supported
workflow.

## Manifest versions

`schemaVersion: 1` is the original frozen contract. `schemaVersion: 2` is additive and adds
directory bundles, capability/dependency declarations, structured installation, and granular
permission disclosure, so a version 1 consumer can continue reading legacy file-form skills:

- `components.skills` entries may be a **directory** containing `SKILL.md` alongside optional
  `scripts/`, `references/`, and `assets/` — not only a path to a lone `SKILL.md` file.
- `components.providers`, the self-describing list of AI model backends a plugin contributes.
- `permissions.execute`, which separates a prompt-only skill from one carrying runnable code,
  plus `permissions.requested`, a granular disclosure such as `sandbox.execute` or
  `network.connect`. Declarations never grant runtime authority; the runtime policy decides the
  actual grant.
- `installation.packages`, which carries an exact package manager, package, version, integrity,
  scope, and expected executables. Raw lifecycle shell commands are not portable or accepted.
- `dependencies.required` and `dependencies.optional`, plus `capabilities.declared` and
  `capabilities.requested`; runtime `detectedCapabilities` are supplied by the host and never
  forged by a manifest.

## Contributing an example

Each plugin lives in its own category folder containing a `chainabit-plugin.json` manifest plus
whatever `components{}` it declares. Add it to `marketplace.json` in the same change. See
[`spec/README.md`](spec/README.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md) for the public
authoring and release contract.
