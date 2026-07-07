---
name: pdf
description: Extracts text, tables, and metadata from local PDF files. Use when the user asks to read, summarize, or pull data out of a PDF already in scope.
license: MIT
---

# PDF Extraction

Given a local PDF path, extract its text content, any tabular data, and document metadata
(title, author, page count). Operate only on files already in the agent's scope — this skill
declares no network or shell access, so it must not attempt to fetch or execute anything.
