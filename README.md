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
| [`skill-pdf/`](skill-pdf/) | `productivity`, `data` | A minimal skill with zero permissions — the calm, low-friction install every plugin should aim for. |
| [`provider-solongate/`](provider-solongate/) | `providers`, `security` | A real partner integration: SolonGate, a security proxy / MCP gateway. Unlike the examples above, this is a real, installable product (`@solongate/proxy` on npm). |
| [`provider-codegraph/`](provider-codegraph/) | `code`, `providers` | A real partner integration: CodeGraph, local-first code intelligence over an MCP server. Declares `workspaceAccess`, so it also illustrates how a plugin discloses reading the user's open project. |

`marketplace.json` at the repo root lists all five by id, version, and source, so any Chainabit
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
one rule cannot be expressed in the schema: a manifest declaring an executing component
(`hooks`, `mcpServers`, or an `install` script) **must** declare `permissions.shell`. That rule
lives in the installing client's parser, so a schema-only check will happily pass a manifest the
client then refuses to install.

## Contributing an example

Each plugin lives in its own top-level folder containing a `chainabit-plugin.json` manifest
(validated against the schema in the main repo) plus whatever `components{}` it declares. Add
it to `marketplace.json` in the same commit — an unlisted folder is unreachable, and a listing
with no folder is a broken install. See the authoring guide in the main NEXUS repository
(`docs/plugins/authoring-guide.md`) before adding a new example here.
