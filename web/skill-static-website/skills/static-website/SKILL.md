---
name: static-website
description: Static HTML/CSS implementation with servability, deterministic output, and link/asset checks; one website variation, not a universal web rule.
license: Apache-2.0
metadata:
  version: 1.1.1
  discovery: "static HTML/CSS implementation and servability checks"
  layer: implementation
  requires: "skill-software-engineering, skill-project-bootstrap, skill-git, skill-project-documentation, skill-web-engineering"
---

# Static website

Choose this variation only after inspecting the requested behavior and available runtime. Preserve the existing website generator/validator behavior: static output has a top-level `index.html`, local or inlined assets, no required build step, and deterministic link/asset/accessibility checks. Use the scripts bundled with this skill; this skill does not imply React, Angular, or any other technology is unavailable.

Build from content and information architecture, then run the generator and validator. Keep source, generated preview, and temporary files distinct. A static implementation is correct only when its actual serving path, links, assets, responsive behavior, and documented commands are verified.

Every script path in this document is written as `{{SKILL_DIR}}/...`. That is a
reference, not a location: the host substitutes it for wherever it actually put
this bundle, which is not a place this package gets to decide or predict. Run
the commands as written. Output paths are relative to your working directory,
which is the workspace root; the scripts write only to the output path they are
given.

The generator needs the host's IBM Plex bundle, which the manifest declares as
the `fonts.ibm-plex` runtime asset; if it is absent the script exits 2 and says
so rather than silently substituting a face. What else the environment has, and
whether you may install more, are properties of the container and its policy
rather than of this skill — call the `workspace.env` tool instead of assuming
either way.

## Registered production path

For a minimal deterministic site, invoke the registered generator directly and
then its authoritative validator:

```bash
python3 {{SKILL_DIR}}/scripts/scaffold_site.py --template landing site
python3 {{SKILL_DIR}}/scripts/validate_site.py site
```

For custom content, print a template spec, edit only the bounded content fields,
validate it, generate the tree, and validate the emitted site:

```bash
python3 {{SKILL_DIR}}/scripts/scaffold_site.py --template landing --print-spec > site.json
python3 {{SKILL_DIR}}/scripts/scaffold_site.py --spec site.json --validate-only
python3 {{SKILL_DIR}}/scripts/scaffold_site.py --spec site.json site
python3 {{SKILL_DIR}}/scripts/validate_site.py site
```

Do not use `--help` as a generation attempt: it emits no artifact identity proof.
The generator writes a top-level `index.html`, local IBM Plex webfonts, and local
CSS with no remote assets or build-time network access. Promote the generated
directory itself only after validation succeeds.
