# Chainabit Plugins — agent instructions

Public, zero-install repository. No `package.json`, no dependencies, Node 20. Everything here is
either **shipped product** or **the tooling that validates it**. Know which one you are touching
before you edit anything.

## Product vs tooling

```mermaid
flowchart TD
    subgraph PRODUCT["Shipped product — pinned by digest"]
        CAT["ai/ artifacts/ cloud/ data/ databases/ devops/<br/>foundations/ frameworks/ infrastructure/ languages/<br/>personas/ providers/ security/ testing/ web/<br/>skill-website/"]
        MP["marketplace.json — 121 entries"]
        SPEC["spec/ — public JSON Schemas"]
    end
    subgraph TOOLING["Repo tooling"]
        VAL["tooling/*.mjs"]
    end
    VAL -->|validates| CAT & MP & SPEC
```

Every category directory is published, versioned marketplace payload. Each `marketplace.json` entry
pins an immutable `revision` plus `integrity.packageSha256`, so **editing a shipped plugin without
bumping its version and regenerating the digest breaks installs.**

Two traps:

- `personas/persona-reviewer/agents/senior-reviewer.md` and `commands/review.md` look exactly like
  Claude Code's `.claude/agents/` and `.claude/commands/` files. They are **marketplace payload**,
  installed into an end user's project. They are not this repo's agent configuration.
- `tooling/` is the one mixed directory. The `*.mjs` files are repo tooling;
  `tooling/skill-cli-design/` and `tooling/skill-developer-tooling/` are product.

## Verify

```bash
node tooling/validate-marketplace.mjs                   # the gate — run before every PR
node --test tooling/marketplace-contract.test.mjs       # semantic fixtures
node tooling/resolve-skills.mjs skill-pdf               # composition resolution
node tooling/validate-marketplace.mjs --write-bundles   # regenerate bundle.json inventories
```

CI runs exactly these four, plus a credential-pattern scan, on PRs and pushes to `main`,
`development`, `development-rebased`.

## Invariants the validator enforces

- A skill's directory name **must equal** the `name` in its `SKILL.md` frontmatter, lowercase.
- A skill containing `scripts/` **must** declare `permissions.execute: true`.
- An executing component **must** declare matching authority.
- Every `marketplace.json` entry **must** carry `integrity.packageSha256`.
- Compatibility plugins **must** declare `aliasOf` and carry no duplicate payload.
- A skill shipping `scripts/` **must** declare `runtime.contractVersion: 2`, address every
  declared entrypoint in `SKILL.md` as `{{SKILL_DIR}}/<path>`, name no host location
  (`/workspace`, `/sandbox`, `.skills`, `/home/…`, `/opt/…`) anywhere in its instructions, and
  declare any absolute path its scripts read as a `runtime.assets` entry.

`skill-website/` exists solely to preserve a historical plugin id. Do not rename or move it.

## A package describes what it needs, never where it will live

`{{SKILL_DIR}}` is a reference the host substitutes for wherever it actually materialized the
bundle. It is not decoration and it is not a convention that can be relaxed for one command.

The rule is written against what happened without it. Four `SKILL.md` files stated that the
skill is materialized at `/workspace/.skills/<skillName>/` — a location this repository does not
control and got wrong, because the runtime uses `<pluginId>-<skillName>`. A fifth carried no
anchoring sentence at all and mixed a bare `scripts/scaffold_site.py` with an absolute
`/workspace/site` output in one command. Production telemetry then shows the predictable result
fourteen times: `can't open file '/workspace/scripts/deck_pptx.py': [Errno 2]`, because the
working directory when a host runs a generator is the workspace root, not the bundle.

Two corollaries that are easy to miss:

- **A path a script PRINTS is an instruction too.** `Next: python3 scripts/validate_pptx.py` was
  emitted on every successful build and was wrong in the only environment that ran it. A script
  knows where it is; sibling invocations are derived from `__file__`, never spelled by hand. A
  path in *another* skill's bundle is not knowable from here at all — say which script, and let
  the host say where.
- **A host path a script READS must be declared.** The artifact font directory was a default
  argument in four scripts and a declared requirement in none, so nothing could tell that the
  packages depended on it. It is now a `runtime.assets` entry, and the validator rejects any
  other absolute path a script hardcodes.

Bundled scripts are published `0644` with a shebang nothing backs, so they are invoked through
the interpreter the manifest declares. `bundle.json` records the mode (`formatVersion: 2`) rather
than leaving a consumer to trust the shebang.

## Review boundaries

`spec/`, `tooling/`, `marketplace.json`, `SECURITY.md`, `security/`, `providers/` are
CODEOWNERS-gated as contract and supply-chain boundaries. Changes there need explicit review.

`registry/` is not a data store — it documents that trust and verification badges are **not**
settable by editing this repo. Absence of a trust record means "not verified", never "verified
false".

## Public repository

This repo is public. Never add internal service or type names, private source paths, internal
infrastructure, unpublished strategy, tenant identifiers, or credentials. `acme.example` and
`internal.invalid` are deliberate non-resolving placeholders — keep them that way.
