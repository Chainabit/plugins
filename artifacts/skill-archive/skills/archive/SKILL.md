---
name: archive
description: Package a file or directory into one deterministic, bounded ZIP and verify the written container before delivering it. Package the smallest valid deliverable set, never a whole working tree.
license: Apache-2.0
metadata:
  version: 1.0.0
---

# Archive packaging

Use this skill when a packaged deliverable is actually required: the user asked
for a ZIP or archive, several requested artifacts should reasonably arrive as
one download, or a deliverable such as a website needs multiple dependent files
to stay together. A directory is not a reason on its own — deliver individual
outputs individually unless a package was requested or is genuinely needed.

```bash
python3 {{SKILL_DIR}}/scripts/build_archive.py site site.zip '{"directories":[],"namespaces":[]}' 1000 67108864 16777216
python3 {{SKILL_DIR}}/scripts/validate_archive.py site.zip
```

Paths are relative to the working directory. The builder takes the source, the
destination, the host's exclusion rules as JSON, and three bounds: the maximum
number of files, the maximum total uncompressed bytes, and the maximum bytes
for any single file.

## Package only the deliverable

Files produced while working are **working files, not deliverables**. Temporary
scripts, intermediate documents, generated sources, caches, logs, validation
output, manifests, internal metadata and debugging files must not be packaged
unless they are genuinely part of what was requested, or the user asked for
them specifically. "Archive everything" is never a reason to package a whole
working tree — select the narrowest source directory that contains the
deliverable, and point the builder at that.

The exclusion rules are supplied by the host, not by this package, and they are
a floor rather than a substitute for choosing a correct source. A rule names
either a directory, matched exactly, or a namespace, which also matches names
entering it at a word boundary.

## Guarantees

Members are written in sorted order, so the same tree always produces the same
archive. Symbolic links and anything that is not a regular file are refused
rather than followed. A member path that would escape extraction is refused. An
archive is never packaged into itself. The bounds above are enforced before any
bytes are written and again against the finished container, including its
expansion ratio.

After writing, the container is reopened and read back; if it does not verify,
it is deleted rather than left behind as a plausible-looking file. Success is
therefore a statement about a file that exists and parses, not an intention.

## Contracts

Successful builder and validator responses use the versioned JSON contracts
`chainabit.archive.execution/v1` and `chainabit.archive.validation/v1`, both
carrying the exact output SHA-256. Exit `1` is a deterministic input or
artifact rejection; exit `2` means an I/O, runtime or internal output failure.
Never infer those categories from human-readable stderr.

Run validation as its own command, or join building and validation with `&&`.
Never place a later command after validation with `;` or a bare newline: its
zero exit status can hide a rejected archive.
