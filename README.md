# Chainabit Plugins — Reference Marketplace

A git-based reference marketplace for the Chainabit plugin format — a cross-platform, versioned
bundle format installable from the NEXUS desktop app, the Chainabit CLI, or the web app. This
repo exists to prove the loop end-to-end: `marketplace add` → `install <id>` with zero backend.

The full manifest spec, JSON Schemas, permission model, and security threat model live in the
main NEXUS repository under `docs/spec/` and `docs/plugins/`.

## What's here

| Plugin | Category | What it illustrates |
|--------|----------|----------------------|
| [`provider-acmegate/`](provider-acmegate/) | `providers`, `security` | A provider-type plugin: lifecycle hooks, an MCP server, and a full dangerous-permission set (`shell`, declared network, `postInstall`/`uninstall` scripts). **Illustrative only** — AcmeGate is a fictional example product, not a real integration. |
| [`persona-reviewer/`](persona-reviewer/) | `personas`, `code` | An installable agent persona + a slash command, with zero permissions requested. |
| [`skill-pdf/`](skill-pdf/) | `productivity`, `data` | A `schemaVersion` 2 executable skill: `components.skills` points at a *directory* holding `SKILL.md` plus `scripts/` and `references/`, and the manifest declares `permissions.execute`. Generates real PDFs; the scripts run in a sandbox, never on the host. |
| [`skill-xlsx/`](skill-xlsx/) | `productivity`, `data` | The same shape applied to spreadsheets. Illustrates a skill whose value is a strict input spec: typed columns in, a real `.xlsx` out, and a validator that catches numbers silently stored as text. |
| [`provider-solongate/`](provider-solongate/) | `providers`, `security` | A real partner integration: SolonGate, a security proxy / MCP gateway. Unlike the examples above, this is a real, installable product (`@solongate/proxy` on npm). |
| [`provider-codegraph/`](provider-codegraph/) | `code`, `providers` | A real partner integration: CodeGraph, local-first code intelligence over an MCP server. Declares `workspaceAccess`, so it also illustrates how a plugin discloses reading the user's open project. |

`marketplace.json` at the repo root lists all six by id, version, and source, so any Chainabit
surface can resolve and install them directly from this repo.

Each listing's `source` is this repo plus a `#<subdirectory>` fragment naming the plugin's own
folder. A client clones the repo and reads the manifest at that fragment, so an id listed
without a matching folder clones cleanly and then fails to install — the listing keeps looking
healthy while every install of it is broken. `marketplace.json` and the folders here must
therefore always describe the same set of plugins; the validator below enforces that.

## Try it

There is no standalone `nexus` binary. Install from the NEXUS desktop app's Marketplace, or
from the `chainabit` CLI (`chainabit-api/apps/cli`, bin names `chainabit`/`cb`) under its
`nexus` command group:

```
chainabit nexus marketplace add https://github.com/chainabit/plugins
chainabit nexus install skill-pdf
```

## Validating

Run before pushing any change to a manifest or to `marketplace.json`:

```
node scripts/validate-marketplace.mjs
```

No dependencies, no install step. It checks every folder's manifest against the frozen
contract, confirms each declared component file exists, and cross-checks `marketplace.json`
against the folders on disk (same ids, same versions, each `source` fragment naming its own
folder). Exits non-zero on the first problem, so it drops straight into a pre-push hook or CI.

It deliberately re-implements the contract rather than only checking the JSON Schema, because
three rules cannot be expressed in the schema:

- A manifest declaring an executing component (`hooks`, `mcpServers`, or an `install` script)
  **must** declare `permissions.shell`.
- A skill directory containing `scripts/` **must** declare `permissions.execute`, so the
  install consent sheet discloses that the plugin ships code meant to be run.
- A skill directory's name **must** equal the `name` in its `SKILL.md` frontmatter. They are two
  declarations of one identity, and nothing else catches them drifting apart.

Those rules live in the installing client's parser, so a schema-only check will happily pass a
manifest the client then refuses to install.

## Manifest versions

`schemaVersion: 1` is the original frozen contract. `schemaVersion: 2` is additive and adds
three things, so a version 1 manifest never has to be rewritten:

- `components.skills` entries may be a **directory** containing `SKILL.md` alongside optional
  `scripts/`, `references/`, and `assets/` — not only a path to a lone `SKILL.md` file.
- `components.providers`, the self-describing list of AI model backends a plugin contributes.
- `permissions.execute`, which separates a prompt-only skill from one carrying runnable code.
  It is a disclosure, not an enforcement point: what actually contains those scripts is the
  sandbox they run in.

## Contributing an example

Each plugin lives in its own top-level folder containing a `chainabit-plugin.json` manifest
(validated against the schema in the main repo) plus whatever `components{}` it declares. Add
it to `marketplace.json` in the same commit — an unlisted folder is unreachable, and a listing
with no folder is a broken install. See the authoring guide in the main NEXUS repository
(`docs/plugins/authoring-guide.md`) before adding a new example here.
