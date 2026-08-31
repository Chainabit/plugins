# Chainabit artifact runtime contract

This document records the production boundary implemented by the active visual
artifact plugins. It is descriptive of the manifests and executable protocols;
prompts are not an enforcement mechanism.

## Lifecycle and ownership

`requested format -> immutable plugin release -> registered generator -> typed
generation evidence -> authoritative validator -> exact byte/tree identity ->
artifact registration -> delivery -> durable evidence -> lease cleanup`

- Generators own rendering mechanics and emit `chainabit.<format>.execution/v1`.
- Validators own structural and quality verification and emit
  `chainabit.<format>.validation/v1` with the exact SHA-256, byte count, MIME,
  output shape, and path they verified.
- The API owns lifecycle transitions, retry policy, proof persistence, exact
  identity comparison, publication eligibility, and idempotency.
- The sandbox bridge owns argv-safe execution and faithful bounded process
  events; it does not interpret PDF, OOXML, or website semantics.
- A broad artifact capability never replaces the requested concrete format.
  A run requesting multiple formats carries one independent contract per
  deliverable.

## Typography policy

The canonical primary family is **IBM Plex Sans**, derived from Chainabit's
product design tokens. `IBM Plex Sans Arabic` is the approved companion for
Arabic-script glyph coverage. Fira Code remains intentionally monospace for
code. The sandbox image installs pinned, checksum-verified upstream IBM font
archives and also exposes the same files under
`/opt/chainabit/artifact-fonts/ibm-plex-sans`; generators never fetch fonts at
render time.

An explicit safe user font takes precedence when the runtime can resolve it.
With no explicit selection, the generator must use IBM Plex Sans. A font name
in CSS or OOXML is not sufficient evidence: format validators inspect embedded
PDF fonts, OOXML theme/run declarations, workbook/document styles, or packaged
website webfonts as appropriate.

## Applicability matrix

| Plugin / skill | Output | Visible text | Previous default/source | Current default and proof | User override | Fallback |
|---|---|---:|---|---|---:|---|
| `skill-pdf-pdf` | PDF | yes | renderer/host dependent | embedded/subset IBM Plex Sans; validator inspects PDF resources | yes, installed safe family | embedded IBM Plex Sans Arabic; Fira Code for code |
| `skill-pptx-pptx` | PPTX | yes | Arial plus incomplete run-only declarations | IBM Plex Sans in theme, defaults, runs and bullets; OOXML validator | yes | IBM Plex Sans Arabic in complex-script declarations |
| `skill-docx-docx` | DOCX | yes | library template defaults | IBM Plex Sans in document styles and run fonts; OOXML validator | yes | IBM Plex Sans Arabic for complex script |
| `skill-xlsx-xlsx` | XLSX | yes | library workbook defaults | IBM Plex Sans in workbook styles/cells; OOXML validator | yes | IBM Plex Sans Arabic for Arabic cells |
| `skill-static-website-static-website` | offline static site tree | yes | browser/system stack | packaged local IBM Plex Sans WOFF2 and CSS design token; tree validator | yes | packaged IBM Plex Sans Arabic; Fira Code for code |
| `skill-website` | none (compatibility alias) | no renderer | delegates | canonical `skill-static-website` contract | delegated | delegated |
| web engineering/framework/testing skills | source guidance, not an artifact renderer | not owned | example-specific | out of typography ownership; generated website delivery is owned by the static-site plugin | n/a | n/a |
| CSV/raw metadata/nonvisual skills | data | no typography semantics | n/a | not applicable | n/a | n/a |

Repository-wide font-token auditing excludes prose that merely names a font,
monospace code semantics, and the deprecated alias. Active visual output owners
must not introduce Inter, Arial, Helvetica, Times, Calibri, Aptos, browser
defaults, external font URLs, or a bare generic family as their primary default.

## Format guarantees

- PDF validation parses the actual PDF object graph (including compressed
  object streams), rejects encryption/blank or non-painted pages, and verifies
  embedded font evidence. A parseable PDF is not automatically acceptable.
- PPTX validation checks ZIP safety, CRC/ratios, required OOXML parts and
  relationships, slide dimensions, text density/overflow, contrast, images,
  and typography declarations.
- DOCX/XLSX validation checks safe OOXML containers, critical relationships,
  content presence, and typography declarations.
- Static websites are output trees with a deterministic manifest hash. The
  validator checks routes, local assets, responsive viewport/layout rules,
  accessibility essentials, offline font packaging, and rejects script or
  external-network dependencies under the static contract.

Fallbacks are not shell commands. A future alternate generator must be declared
by an immutable plugin release, emit the same generation evidence protocol, and
pass the same authoritative validator before it can be published.
