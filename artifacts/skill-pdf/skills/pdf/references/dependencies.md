# Production dependency profile

The official Markdown PDF contract has explicit third-party runtime dependencies. The production sandbox image pins and probes:

```text
weasyprint 69.0   HTML/CSS pagination, embedded fonts, tables, images, RGB styling
reportlab    programmatic tables and page-flow reports
Pillow       bounded image decode, validation, orientation and normalization
pypdf 6.16.2     authoritative parsing, page/content validation, exact hashing
```

IBM Plex Sans is the required offline primary family, with IBM Plex Sans Arabic as the approved script companion. Importability alone is not a capability: image build checks and the skill's production-profile suite must exercise rendering, font embedding and object-stream validation. Dependencies must not be downloaded during a user run, and network access remains disabled. Math and CJK/RTL claims require an adapter-specific integration test before being added to a descriptor.
