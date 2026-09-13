---
name: brand-defaults
description: Shared Chainabit visual-default policy for websites, PDFs, and presentations; resolve user identity before applying platform defaults.
license: Apache-2.0
metadata:
  version: 1.0.0
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
3. Only when neither is present, use the Chainabit default profile.

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
