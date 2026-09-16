# Production dependency profile

The official Markdown PDF contract has explicit third-party runtime dependencies. The production sandbox image pins and probes:

```text
weasyprint 69.0   HTML/CSS pagination, embedded fonts, tables, images, RGB styling
markdown 3.8.2    Markdown parsing, read through the skill's CommonMark block adapter
reportlab    programmatic tables and page-flow reports
Pillow       bounded image decode, validation, orientation and normalization
pypdf 6.16.2     authoritative parsing, page/content validation, exact hashing
```

IBM Plex Sans is the required offline primary family, with IBM Plex Sans Arabic as the approved script companion. Importability alone is not a capability: image build checks and the skill's production-profile suite must exercise rendering, font embedding and object-stream validation. Dependencies must not be downloaded during a user run, and network access remains disabled. Math and CJK/RTL claims require an adapter-specific integration test before being added to a descriptor.

RTL has that test and that font coverage today: WeasyPrint's Unicode Bidi and Arabic shaping run through Pango/HarfBuzz regardless of which font paints the glyphs, and IBM Plex Sans Arabic is the declared, embedded companion for the Arabic/Hebrew range. CJK does not have either yet. No font in this image — not IBM Plex Sans, not IBM Plex Sans Arabic, not the base image's `fonts-dejavu-core` — carries a single CJK glyph (checked against the actual released font files, not assumed), so `cjk` is deliberately absent from the WeasyPrint capability descriptor in `backends.py` until a CJK-capable family is added to the runtime image and pinned the same way the two families above are. Until then, a document whose content requires `cjk` is refused by the capability resolver with a named, machine-readable reason rather than rendered with missing glyphs.
