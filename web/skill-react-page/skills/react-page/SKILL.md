---
name: react-page
description: Build a React page, app, dashboard or slide presentation offline in the sandbox and deliver it as one self-contained HTML file. Use it whenever the user asks for React; use static-website only for plain pages with no scripting.
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "React page or app built offline into one self-contained HTML file"
  layer: implementation
  requires: "skill-brand-defaults, skill-react, skill-web-engineering"
---

# React page

Write the page as ordinary React source, build it in the sandbox with the script
below, and deliver the single HTML file it produces. The sandbox has no network,
so the build uses the toolchain the host already carries (React, ReactDOM and a
bundler); nothing is downloaded, and the finished file loads nothing from
another address.

Every script path in this document is written as `{{SKILL_DIR}}/...`. That is a
reference, not a location: the host substitutes it for wherever it actually put
this bundle. Run the commands as written. Output paths are relative to your
working directory, which is the workspace root.

## Choose this skill when

The user asks for React, or for anything that needs scripting or components: an
app, a tool, a dashboard, a game, an interactive slide presentation, a page with
state. A page with no scripting at all is `static-website`. Do not load React,
Babel or any library from a CDN, and do not paste a library into a tool call:
the file is served where nothing can be fetched.

## Write the source

Create the source files with `workspace.files`, for example `app/App.jsx` plus
any other components, `.css` files and images it imports.

- `app/App.jsx` has a **default export**: the root component. The build mounts
  it; do not write a `main.jsx`, `index.html` or `ReactDOM.createRoot` call.
- Import CSS from a component (`import './styles.css'`) and images by relative
  path (`import logo from './logo.png'`). Both end up inside the one file.
- `.jsx`, `.tsx`, `.js` and `.ts` are all accepted. Plain function components
  and hooks; no router, no server, no package to install.
- Everything the page shows is in the source. There is no network at runtime, so
  no `fetch`, no remote images, no web fonts, no form that posts anywhere. Put
  the data in the source: a module, or a `.json` file you write and import.
- Write the content in the user's language and pass that language as `--lang`.
- Resolve the visual identity first, through `skill-brand-defaults`: an explicit
  brand, font, palette or reference from the user wins; only without one use the
  Chainabit defaults. Do not blend them.

For a slide presentation: one component per slide over a data array, one slide
visible at a time, filling the viewport, with keyboard navigation (arrow keys,
Home, End), visible previous/next buttons, a "3 / 12" position that is
announced (`aria-live="polite"`), and a `prefers-reduced-motion` respect. Give
every slide a heading and every image an `alt`. Make it read on a phone.

## Build

```bash
python3 {{SKILL_DIR}}/scripts/build_react_page.py \
  --entry app/App.jsx --out page/index.html \
  --lang tr --title "Page title"
```

- `--lang` is a BCP-47 tag (`en`, `tr`, `ar-EG`). It becomes `<html lang>` and
  sets `dir`: Arabic, Hebrew, Persian and Urdu render right to left.
- IBM Plex Sans is embedded by default. Pass `--font none` when the user named a
  different font or wants the system stack, and style it in your own CSS.
- `--description` adds a meta description.

On success the last line of output is a JSON record with the exact `sha256` and
`bytes` of the file. The script exits 1 for a bad argument or entry, 2 when the
host lacks the toolchain, 3 when the bundle or the page fails its checks; the
message names the file, the line, or the rule. Fix the source and run it again.
On exit 2, say plainly that this environment cannot build React pages; do not
fall back to a CDN or a hand-written imitation. Call `workspace.env` rather
than guessing what the environment has.

## Deliver

Promote the one file with `artifact.create`, `outputPath` set to the `--out` path
(`page/index.html`) and `kind` `website`. Promote the file itself, not its
folder and not a ZIP. Do not use `artifact.write` for it: that tool stores only
text you typed, and a bundled page is far larger than that.

The platform checks the file against the single-file page contract; the script
already applies the same rules, so a refusal here means the source did
something the build could not see, such as creating an `<img>` with a remote
address at run time. Remove it and build again.

Tell the user what was built and that it opens as a single file; describe what
you verified. The script proves the page is self-contained and well formed. It
does not run the page, so do not claim you clicked through it.
