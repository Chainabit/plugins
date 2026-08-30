# Optional dependency profiles

The core skill intentionally has no third-party PDF runtime dependency. Install a professional environment with the project's Python interpreter using the audited optional set appropriate to deployment:

```text
weasyprint   HTML/CSS pagination, embedded fonts, tables, images, RGB styling
reportlab    programmatic tables and page-flow reports
Pillow       bounded image decode, validation, orientation and normalization
pypdf        merge/extract/reorder/rotate/crop manipulation
```

These packages are detected at runtime and remain absent from the minimal configuration. A package is not considered a rendering capability merely because it imports: the registry descriptor must list the behavior and tests must exercise it. CI should run both the dependency-free suite and a professional environment with WeasyPrint (plus the image boundary) installed. Math and CJK/RTL claims require an adapter-specific integration test before being added to a descriptor.
