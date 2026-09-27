---
name: brand-defaults
description: Shared Chainabit visual-default policy for websites, PDFs, and presentations; resolve user identity before applying platform defaults.
license: Apache-2.0
metadata:
  version: 1.1.0
  layer: foundation
---

# Chainabit brand defaults

Read `references/brand-profile.json` before making a visual artifact. It is the
single distributed source for the default identity and the resolution rules used
by composed website, PDF, and presentation skills.

Resolve visual identity once, before creating an artifact spec:

1. Explicit user branding — a named brand, font, palette, theme, template,
   visual language, logo, or supplied visual reference — wins.
2. A supplied artifact-specific brand guide, template, or reference wins when
   the user has not named a competing identity.
3. Relevant project branding wins over platform defaults. A project’s UI
   marker colour is not a brand guide. Use its explicitly configured artifact
   appearance, terminology, logo and reusable asset references where relevant.
4. A project’s default template applies when no competing identity is present.
5. Otherwise use the Chainabit default profile: neutral light/dark surfaces,
   with green restricted to accents in text, icons, controls, states and borders.
   Do not introduce a blue/purple palette or gradient as an automatic default.

Do not blend identities. An explicit identity is not a request to add Chainabit
colours, typography, a logo, or a footer afterwards. When a competing font,
style, brand, or reference is named, encode both its font and a complete palette
in the target skill's documented fields; complete custom palettes replace the
default palette rather than inheriting omitted brand roles. A typeface-only
request may retain neutral layout treatment, but must not add a Chainabit mark
or footer.

Chainabit defaults are platform defaults, not a constraint on the user's work.
Use local, supplied, or runtime-declared assets only. Never introduce a remote
font or logo dependency to satisfy a brand request.

## Structured composition and reusable assets

When the runtime supplies resolved composition data, use its common appearance
or the matching format template appearance. Generation overrides win; never
modify the original template. Map fontFamily to the target spec’s documented
font field and the selected complete palette to its palette fields. Keep
pagination, slide geometry and responsive layout in the target renderer’s spec.
Missing configuration uses defaults. Font availability and contrast are still
validated by that renderer; do not invent a remote dependency to fill a gap.

Use admitted asset references before generating equivalent media. Materialize
only the relevant logo, image, illustration, screenshot or diagram through the
available file capability; preserve source identities and provenance. Missing or
deleted assets may be omitted or replaced only when needed to answer the request.
Choose charts for quantitative relationships, diagrams for systems/processes,
and tables for compact comparisons. Avoid decorative repetition. Use retrieved
evidence for factual project knowledge rather than copying an entire library.

Appearance resolution is independent of presentation mode. An inline-capable
interactive explanation does not require a downloadable file or deployed site.
Use the host’s selected presentation capability; persistent files and websites
still use their normal generators when the requested outcome needs them.
