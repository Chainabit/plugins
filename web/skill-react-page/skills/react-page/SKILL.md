---
name: react-page
description: Use when the user explicitly wants a React page, interactive dashboard or presentation, or a 3D scene, WebGL animation or three.js game, delivered as one self-contained HTML file that opens offline; React source is compiled with a build step and three.js is available offline. Do not use for a multi-page HTML/CSS website with separate pages and links, or when no build step is wanted (use static-website), or for a comprehensive React application (use react).
license: Apache-2.0
metadata:
  version: 1.2.0
  discovery: "Explicit single-file offline React deliverable with a build step, including 3D, WebGL, three.js games and animations; multi-page HTML/CSS sites use static-website"
  layer: implementation
  requires: "skill-brand-defaults, skill-react, skill-web-engineering"
---

# React page

Write the page as ordinary React source, build it in the sandbox with the script
below, and deliver the single HTML file it produces. The sandbox has no network,
so the build uses the toolchain the host already carries (React, ReactDOM,
three.js 0.187.0 and a bundler); nothing is downloaded, and the finished file loads nothing from
another address.

Every script path in this document is written as `{{SKILL_DIR}}/...`. That is a
reference, not a location: the host substitutes it for wherever it actually put
this bundle. Run the commands as written. Output paths are relative to your
working directory, which is the workspace root.

## Choose this skill when

The requested deliverable is one self-contained HTML file with React components,
such as an offline page or an interactive slide presentation. React alone does
not imply this delivery format. For a comprehensive application with separate
modules, routing, build configuration or server behavior, inspect the React
application capability and choose its actual structure. A small inline-capable
conversation experience need not create a file. Do not load React,
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

## 3D scenes, animations and games (three.js)

three.js 0.187.0 is part of the toolchain, so a 3D scene, a WebGL animation or a
small game is an ordinary React page. Import it as `import * as THREE from 'three'`
and its addons from `three/addons/...`, for example
`import { OrbitControls } from 'three/addons/controls/OrbitControls.js'`. It is
bundled into the one file; never load it from a CDN or an import map, and do not
paste the library into a tool call. A page that imports `three` on a host whose
toolchain lacks it makes the build exit 2.

Write the scene as one component that owns a canvas:

- Keep the canvas in a `ref`. Create the renderer, scene, camera and
  `requestAnimationFrame` loop inside one `useEffect`, and return a cleanup that
  cancels the frame, removes every listener and observer, disposes geometries,
  materials and textures, and calls `renderer.dispose()`. React may run the
  effect twice in development; the cleanup must make that harmless.
- Keep per-frame state (positions, score timers, input) in plain variables or
  refs inside the effect, not in React state that re-renders every frame. Use
  React state only for what the interface shows, such as score or a pause menu,
  and update it rarely.
- Size from the container: a `ResizeObserver` on the canvas parent calls
  `renderer.setSize(width, height, false)` and updates `camera.aspect`. Cap
  `renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))`.
- Drive animation from the clock delta (`THREE.Clock`), not from a fixed step
  per frame, and clamp the delta so a background tab does not teleport the game.
  Pause the loop on `document.visibilitychange`.
- Honor `prefers-reduced-motion`: read
  `window.matchMedia('(prefers-reduced-motion: reduce)')`; for a decorative
  animation render a still or slow motion, and for a game keep it playable and
  skip camera shake, flashes and parallax.
- Input: keyboard through `keydown`/`keyup` on `window`, pointer through
  `pointerdown`/`pointermove`/`pointerup` on the canvas with `touch-action: none`
  so a phone can play it. Show the controls on screen, give the canvas
  `tabIndex={0}` and an `aria-label`, and add visible start, pause and restart
  buttons. Game over needs a visible state and a way to play again.
- Everything is generated in code or imported from the source folder: built-in
  geometries and materials, procedural textures from a `<canvas>`, small images
  imported by relative path. No `TextureLoader` or `GLTFLoader` fetch of a remote
  address; a model or texture is a `data:` import you wrote yourself.
- WebGL can be unavailable. Create the renderer inside a `try`/`catch` (or test
  `canvas.getContext('webgl2')` first) and, when it fails, render a visible
  message in the page language that says 3D is not available here, with a
  static description of what the scene shows. Never leave a blank canvas.
- Fonts in the scene: use HTML overlays for text and the score rather than
  `FontLoader` or `TextGeometry`, which need a font file.

Build size: the three.js core adds roughly 0.7 MB to the page and addons a
little more; the page limit leaves room for that, but import only the addons
you use and avoid large embedded images.

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
On exit 2, say plainly that this environment cannot build this page; do not
fall back to a CDN or a hand-written imitation. Call `workspace.env` rather
than guessing what the environment has.

## Deliver

Promote the one file with `artifact.create`, `outputPath` set to the `--out` path
(`page/index.html`) and `kind` `website`. Promote the file itself, not its
folder and not a ZIP. Do not use `artifact.write` for it: that tool stores only
text you typed, and a bundled page is far larger than that.

If `artifact.create` refuses the file, the source did something the build could
not see, such as creating an `<img>` with a remote address at run time. Remove
it and build again.

Tell the user what was built and that it opens as a single file; describe what
you verified. The script proves the page is self-contained and well formed. It
does not run the page, so do not claim you clicked through it.
