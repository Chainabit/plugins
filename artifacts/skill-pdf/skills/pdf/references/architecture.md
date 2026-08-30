# PDF architecture and quality contract

`PdfService` is the application controller. `DocumentRequirements` is the information expert for semantic needs; `BackendResolver` owns strategy selection; `SecurityPolicy` owns trust boundaries; image/font/math boundaries normalize or reject assets; renderer adapters own provider APIs; verification owns structural and semantic output checks; temporary resources and atomic persistence own lifecycle.

Adapters are protected variations. Their domain input is plain strings, mappings, and geometry, never WeasyPrint, ReportLab, Pillow, or pypdf objects. Registry capabilities describe tested behavior only. A resolver decision must satisfy every requirement, and its rejection reasons are diagnostic data.

The minimal renderer is intentionally not a quality ceiling. It is deterministic and portable, but accepts only ASCII printable basic text. A rich request must fail when its adapter or dependencies are absent. Never treat a structurally valid PDF as visually correct: acceptance also requires required text, glyphs, tables, images, equations, layout, and color semantics to survive.

HTML/CSS rendering receives sanitized generated HTML, data-URI assets validated through the image boundary, and a fetcher that rejects network/file URLs. Future executable math adapters must use resolved executable paths, argument arrays, isolated temporary directories, resource limits, and timeouts; unrestricted LaTeX execution is not a capability.
