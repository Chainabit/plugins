---
name: static-website
description: Static HTML/CSS implementation with servability, deterministic output, and link/asset checks; one website variation, not a universal web rule.
license: Apache-2.0
metadata:
  version: 1.0.0
  discovery: "static HTML/CSS implementation and servability checks"
  layer: implementation
  requires: "skill-software-engineering, skill-project-bootstrap, skill-git, skill-project-documentation, skill-web-engineering"
---

# Static website

Choose this variation only after inspecting the requested behavior and available runtime. Preserve the existing website generator/validator behavior: static output has a top-level `index.html`, local or inlined assets, no required build step, and deterministic link/asset/accessibility checks. Use the scripts bundled with this skill; this skill does not imply React, Angular, or any other technology is unavailable.

Build from content and information architecture, then run the generator and validator. Keep source, generated preview, and temporary files distinct. A static implementation is correct only when its actual serving path, links, assets, responsive behavior, and documented commands are verified.
