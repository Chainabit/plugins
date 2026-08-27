---
name: website
description: Builds a real static website inside the sandbox from a JSON spec or one of three templates, with a checked palette, type scale and responsive breakpoints, and verifies every page and link before it is handed back. Use when the deliverable is a website or web page - the request mentions a website, a site, a landing page, a portfolio, a homepage, a blog, "web sitesi", or asks for HTML/CSS to open in a browser. Also use to check whether an existing site directory is servable and its links and assets resolve. Do NOT use when the deliverable is a document or report to be read - use the pdf skill; nor for a slide deck (.pptx) or spreadsheet (.xlsx). Static HTML/CSS only: there is no Node.js in the sandbox, so React, Vite and Next are out of scope. Sets Turkish and other Latin Extended-A text correctly.
license: Apache-2.0
metadata:
  version: 1.1.0
---

# Static website generation

## Overview

- **`scaffold_site.py`** — spec in, site out. You supply content; it decides
  layout, palette, type scale and breakpoints. There is no per-page styling
  knob, because that is where inconsistent sites come from.
- **`validate_site.py`** — the exit gate. Catches what raises no exception: a
  missing or nested entry point, links and assets resolving to nothing, remote
  assets that cannot load, `<img>` with no `alt`, a page with no `<title>`.
  Nothing is done until it exits 0.

Script paths below are **relative to this skill's directory**: at
`/workspace/.skills/website/`, `scripts/scaffold_site.py` means
`/workspace/.skills/website/scripts/scaffold_site.py`.

## Scope: what "website" means here

**Static HTML, CSS and vanilla JavaScript. No build step.** A constraint of the
environment, not a style preference:

- **No Node.js, npm, yarn or pnpm in the sandbox.** The image ships a Python
  toolchain only, so `npm install`, `npx`, `vite` and `next` all fail. **React,
  Vue, Svelte, Vite, Next.js, the Tailwind CLI and every bundler are out of
  scope** until a Node toolchain lands in the image.
- **Egress is deny-all.** Nothing is fetchable at build time and nothing remote
  is linkable: no CDN `<script>`, no Google Fonts `@import`, no remote image. An
  asset lives in the site directory or is inlined as a `data:` URI.
- **Scripts are Python 3 stdlib only**, so they need no setup step and no
  template engine. That is a property of these scripts, not a claim about the
  container: call the `workspace.env` tool to see what is installed and whether
  installs are permitted before concluding a library is out of reach.

A static site is not a consolation prize: system fonts, one stylesheet and no
JavaScript is the fastest a page can be. If a request truly needs a framework,
say so rather than emitting a React file nothing can build.

## The entry point rule

**`index.html` must sit at the top level of the output directory**, not one level
down in `site/` or `dist/`.

A promoted website artifact is served by its entry point: the API refuses a
`kind: 'website'` promotion whose `index.html` is nested, and a tree with none
publishes to a preview resolving to nothing. Both scripts enforce this first, and
report a nested entry point separately from a missing one — the fix differs.

## Task: build a site

From a template, or via its spec — dump, edit, rebuild:

```bash
python3 scripts/scaffold_site.py --template portfolio /workspace/site
python3 scripts/validate_site.py /workspace/site        # OK: ... entry point index.html

python3 scripts/scaffold_site.py --template landing --print-spec > /workspace/spec.json
python3 scripts/scaffold_site.py --spec /workspace/spec.json --validate-only
python3 scripts/scaffold_site.py --spec /workspace/spec.json /workspace/site
```

`--validate-only` checks the whole spec and writes nothing, so a malformed spec
costs one fast run instead of a broken site. An `ERROR:` from either script means
the site is not deliverable: fix the cause and re-run, and never describe a
failed build as a finished site.

Templates: `landing` (one page, no nav) for a product or campaign page;
`portfolio` (flat multi-page) for a personal site, agency or small business;
`blog` (with a `posts/` directory) for anything with article pages.

### The spec

```json
{
  "site": { "title": "Aylin Demir", "theme": "auto", "accent": "#1D4ED8" },
  "pages": [
    { "path": "index.html", "title": "Home", "nav": "Home", "sections": [
        { "type": "hero", "heading": "Aylin Demir", "text": "Short lede.",
          "actions": [{ "label": "See work", "href": "work.html" }] },
        { "type": "cards", "id": "work", "heading": "Selected work",
          "items": [{ "title": "A project", "meta": "2026", "href": "work.html" }] }
      ] }
  ]
}
```

Site: `title` (required); optional `tagline`, `description` (the meta
description), `lang` (default `en`), `theme` (`auto` default, `light`, `dark`),
`accent`, `accentDark`, `footer`.

Page: `path` (required, relative, ends `.html`), `title` (required), `sections`
(required, 1–12). `nav` puts the page in the navigation; omit it and something
else must link there, or the validator warns nothing leads a visitor to it.

Section types, each taking an optional `id` so `#fragment` links land:

| `type` | Fields | For |
|--------|--------|-----|
| `hero` | `heading`, `text?`, `actions?` (max 2) | the page's `<h1>` |
| `prose` | `heading?`, `paragraphs` | narrative copy |
| `features` | `heading?`, `items` (max 9) | a grid of short value props |
| `cards` | `heading?`, `items` | a grid of linked things |
| `list` | `heading?`, `items` | a stacked list: posts, projects, roles |
| `contact` | `heading?`, `text?`, `links` | contact details |

**Every page's first section must be a `hero`, and there must be exactly one** —
it renders the single `<h1>`, and a page without one has no heading to navigate
by. `items` take `title` plus optional `meta`, `text`, `href`.

There are no other section types and no styling fields, on purpose: colour, size
and spacing follow from a section's role, not a per-section override.

Every problem is reported in one run, addressed by its path in the spec — e.g.
`ERROR: pages[0].sections[0]: must be a 'hero'; found 'prose'.` Correct all of
them at once and re-run. Do not fix one and retry.

## Task: validate a site

```bash
python3 scripts/validate_site.py <directory> [--strict]
```

Per file it reports: a missing entry point, and separately a nested one; broken
relative links and asset references, with the path each resolved to; absolute
`http(s)` asset references; root-absolute paths, which break under the version
prefix a promoted site is served from; `<img>` without `alt`; a missing or empty
`<title>`; a missing viewport meta; fragments naming ids nothing declares; empty
pages; duplicate ids; and pages nothing links to.

Exit `0` means deliverable, though `WARNING:` lines may still appear — read them.
Exit `1` means do not hand it to the user; every `ERROR:` names the file, the
line and the change to make. `--strict` promotes warnings to failures.

An outbound `<a href="https://...">` is **not** flagged: the visitor's browser
opens it later, and that browser has a network. Only assets fetched while the
page renders are errors.

Stdlib-only, and it reads the files themselves, so it works on any site.

## Working pattern

1. **Plan.** The page list first, as paths and titles only. A page that cannot
   be named in a word is doing two jobs.
2. **Validate.** Write the spec, run `--validate-only`, fix every `ERROR:`.
3. **Execute.** Build the site, then run `validate_site.py`.

## Reference

`references/design.md` — contrast minimums with measured ratios, both palettes,
the fluid type scale, the spacing rhythm, the three breakpoints and why they sit
there, and the accessibility floor. Read before overriding a colour, a size or a
breakpoint.

## Script quick reference

| Task | Command (prefix `python3 scripts/`) |
|------|--------------------------------------|
| Build from a template | `scaffold_site.py --template portfolio ./site` |
| Dump a spec to edit | `scaffold_site.py --template blog --print-spec > spec.json` |
| Check a spec first | `scaffold_site.py --spec spec.json --validate-only` |
| Confirm a site is servable | `validate_site.py ./site [--strict]` |

Both accept `--help`.
