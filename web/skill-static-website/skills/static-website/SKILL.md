---
name: static-website
description: Static HTML/CSS implementation with servability, deterministic output, and link/asset checks; one website variation, not a universal web rule.
license: Apache-2.0
metadata:
  version: 1.3.3
  discovery: "static HTML/CSS implementation and servability checks"
  layer: implementation
  requires: "skill-software-engineering, skill-project-bootstrap, skill-git, skill-project-documentation, skill-web-engineering"
---

# Static website

Choose this variation only after inspecting the requested behavior and available runtime. Preserve the existing website generator/validator behavior: static output has a top-level `index.html`, local or inlined assets, no required build step, and deterministic link/asset/accessibility checks. Use the scripts bundled with this skill; this skill does not imply React, Angular, or any other technology is unavailable.

Build from content and information architecture, then run the generator and validator. Keep source, generated preview, and temporary files distinct. A static implementation is correct only when its actual serving path, links, assets, responsive behavior, and documented commands are verified.

Every script path in this document is written as `{{SKILL_DIR}}/...`. That is a
reference, not a location: the host substitutes it for wherever it actually put
this bundle, which is not a place this package gets to decide or predict. Run
the commands as written. Output paths are relative to your working directory,
which is the workspace root; the scripts write only to the output path they are
given.

The default generator needs the host's IBM Plex bundle, which the manifest
declares as the `fonts.ibm-plex` runtime asset; if it is absent the script exits
2 rather than silently substituting a face. A competing user family does not
fetch a font or require that bundle. What else the environment has, and whether
you may install more, are properties of the container and its policy rather than
of this skill — call the `workspace.env` tool instead of assuming either way.

`skill-brand-defaults` is a composed foundation. Resolve the user or supplied
artifact identity through it before writing this spec. With no visual identity,
the generated site uses the Chainabit default palette and IBM Plex Sans. For a
competing font, style, brand, or reference, emit its font and a complete palette
in the spec; never add Chainabit styling back in.

## Registered production path

For a minimal deterministic site, invoke the registered generator directly and
then its authoritative validator:

```bash
python3 {{SKILL_DIR}}/scripts/scaffold_site.py --template landing site
python3 {{SKILL_DIR}}/scripts/validate_site.py site
```

For custom content, print a template spec, edit only the bounded content fields,
validate it, generate the tree, and validate the emitted site:

```bash
python3 {{SKILL_DIR}}/scripts/scaffold_site.py --template landing --print-spec > site.json
python3 {{SKILL_DIR}}/scripts/scaffold_site.py --spec site.json --validate-only
python3 {{SKILL_DIR}}/scripts/scaffold_site.py --spec site.json site
python3 {{SKILL_DIR}}/scripts/validate_site.py site
```

Do not use `--help` as a generation attempt: it emits no artifact identity proof.
The generator writes a top-level `index.html` and local CSS with no remote assets
or build-time network access; the Chainabit default additionally packages local
IBM Plex webfonts. Promote the generated directory itself only after validation
succeeds.

## Visual identity fields

`site.font` is a safe named CSS family; omit it for the locally packaged IBM
Plex Sans default. A competing name such as `Inter` stays first in a
local/system `sans-serif` stack and is never fetched remotely or followed by a
Chainabit fallback. A non-Chainabit `site.font` requires `site.palette`, so an
explicit typeface cannot accidentally inherit Chainabit colours. The existing
`accent` and `accentDark` fields remain a narrow accent override. For a
different visual identity, use `site.palette` instead: it must provide every
role (`background`, `surface`, `ink`, `body`, `muted`, `rule`, `accent`, and
`accentInk`) for every active theme (`light`, `dark`, or both for `auto`). A
complete palette deliberately has no Chainabit fallback roles, so it cannot
silently co-brand a customer site. Do not combine `palette` with
`accent`/`accentDark`.

## Language and direction

`site.lang` is the one field that declares the site's content language (an
IETF/BCP-47 tag such as `"en"`, `"tr"`, `"ar"`, `"ar-EG"`); it becomes the
`<html lang>` every page carries and drives `<html dir>` as well. Direction
is derived from `lang`'s primary subtag -- `ar`, `he`, `fa`, `ur`, `ps`,
`sd`, `ug`, `yi`, `dv`, `prs` render `dir="rtl"`; anything else renders
`dir="ltr"`. There is no separate direction field to set: write the content
in the requested language and declare `site.lang` once. The generated
stylesheet uses logical CSS properties (`padding-inline`, `inset-inline-start`)
that follow `dir` automatically in every evergreen browser, so a right-to-left
site is not naive string reversal and is not a second styling system.

`site.skipLinkLabel` and `site.navLabel` override the two pieces of
accessibility chrome this generator writes on every page -- the "Skip to
content" link and the primary navigation's `aria-label` -- which otherwise
default to English regardless of `site.lang`. Set them when the site's
declared language is not English so a screen-reader user hears the page's
own language throughout, not a mix.

## Images

A `hero` section, and each item of a `features` or `cards` section, accepts an
optional `image: {"src": "...", "alt": "..."}`. `src` is a path to a real
image file (`.png`, `.jpg`, `.jpeg`, `.gif`, or `.webp`) sitting next to the
spec file on disk, resolved and validated relative to it — never a URL or a
`data:` URI, because generation has no network access. `alt` is required and
describes the image for a screen-reader user. Stage the file next to the spec
before running the generator (for example, copy a Chainabit-owned generated or
uploaded asset into the sandbox by its file id first); the generator then
copies it into the site's own `assets/images/` under a content-derived name
and rewrites the reference, so the same source image used twice copies once
and re-running the same spec never duplicates it. A `list` item does not
render an image field even if one is supplied.
