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

`marketplace.json` at the repo root lists all three by id, version, and source, so any Chainabit
surface can resolve and install them directly from this repo.

## Try it

```
nexus marketplace add https://github.com/chainabit/plugins
nexus install skill-pdf
```

## Contributing an example

Each plugin lives in its own top-level folder containing a `chainabit-plugin.json` manifest
(validated against the schema in the main repo) plus whatever `components{}` it declares. See
the authoring guide in the main NEXUS repository (`docs/plugins/authoring-guide.md`) before
adding a new example here.
