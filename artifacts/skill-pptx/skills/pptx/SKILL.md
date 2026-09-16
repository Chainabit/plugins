---
name: pptx
description: Builds real .pptx presentations inside the sandbox from a JSON spec, using eight brand-safe layouts with a checked palette and type scale - four text layouts, three that embed a picture, and one that plots a chart - renders that same spec as a matching PDF, and verifies the result is presentable before it is handed back. Use when the requested deliverable is a slide deck: the request mentions PowerPoint, pptx, .pptx, slides, a deck, a presentation, a pitch, "sunum", "slayt", a board update or a talk - including when it also asks for a PDF of that deck, for a picture or chart on the slides, or for the deck in several formats at once. Also use to check whether an existing .pptx is valid, or whether its slides are empty, overflowing, unreadably small, or too dense, and how many pictures and charts it embeds. Do NOT use when the deliverable is a document or report meant to be read rather than shown - use the pdf skill; do NOT use for a spreadsheet (.xlsx), a Markdown outline, or a web page. Renders Turkish and other Latin Extended-A characters correctly.
license: Apache-2.0
metadata:
  version: 1.4.3
---

# Presentation generation

## Overview

Three scripts, one spec. Two of them render that spec into a format; the third
is the check a deck is held to.

- **`deck_pptx.py`** — JSON spec in, `.pptx` out. You supply content; the script
  decides layout, palette and type size. There is no per-slide styling knob,
  because that is where bad decks come from.
- **`deck_pdf.py`** — the same spec in, `.pdf` out, one page per slide. Not a
  converter: it reads the spec, not the `.pptx`, and shares the other script's
  layout engine, so the two outputs agree by construction instead of by
  inspection. This is what a request for "the deck and a PDF" needs.
- **`validate_pptx.py`** — the deck's validator, `skill-pptx.validate_pptx`. It
  catches the defects that raise no exception: empty slides, text spilling out
  of its box, contrast below the readable floor, type under the size floor,
  over-dense bullet lists. It also reports how many pictures and charts the deck
  actually embeds, per slide and in total. Delivery runs it on every deck this
  skill builds; run it yourself only to check a deck you did not build here.

Every script path in this document is written as `{{SKILL_DIR}}/...`. That is a
reference, not a location: the host substitutes it for wherever it actually put
this bundle, which is not a place this package gets to decide or predict. Run
the commands as written. Output paths are relative to your working directory,
which is the workspace root; the scripts write only to the output path they are
given.

`python-pptx`, `reportlab` and `Pillow` are **already installed** in the sandbox
image, so both scripts run with no setup step. Do not guess at what else is installed or
whether you may install it — that is not a fixed property of this skill, it is a
property of the container and the lease's policy, and it changes. Call the
`workspace.env` tool: it reports the interpreters, the installed packages, the
binaries on `PATH`, and whether installing more is permitted right now. Nothing
else in this document makes a claim about the environment.

`skill-brand-defaults` is a composed foundation. Resolve visual identity before
creating a deck spec: an explicit user brand, supplied template/reference, font,
palette, or design language wins. With none of those signals, the renderer uses
the Chainabit default presentation system. Never add Chainabit styling back into
an explicitly branded deck. For a competing font, style, brand, or reference,
write both the font and a complete `palette` into the spec. A non-Chainabit
`font` without that complete palette is rejected rather than inheriting
Chainabit colours.

A deck is for talking over. If the deliverable is meant to be *read* — a report,
a memo, a one-pager — build a PDF with the `pdf` skill instead. A document
rendered as slides is the worst artefact of the pair: too shallow to read, too
dense to show.

That is a different question from **which files to hand back**, and the two get
confused. A request very often asks for the deck *and* a PDF of it, to send to
people who will not open PowerPoint. That is one deck in two formats, not two
deliverables — build both from the one spec (see *Task: build a deck*) and
promote both. Delivering only the `.pptx` when a PDF was also asked for is an
unfinished job, not a judgement call about the medium.

## Quick start

```bash
cat > spec.json <<'EOF'
{
  "title": "Q3 Operations Review",
  "author": "Operations",
  "slides": [
    { "layout": "title", "title": "Q3 Operations Review",
      "subtitle": "Regional summary", "meta": "Operations · 21 August 2026" },
    { "layout": "content", "title": "Where volume came from",
      "bullets": ["İstanbul led growth at +18%", "İzmir held flat",
                  "Şanlıurfa slipped 4% on staffing"],
      "note": "Excludes intra-company transfers.",
      "notes": "Cover the depot openings before taking questions." },
    { "layout": "comparison", "title": "Build versus buy",
      "left":  { "heading": "Build", "bullets": ["Full control", "No licence cost"] },
      "right": { "heading": "Buy",   "bullets": ["Live in six weeks", "Vendor owns risk"] } },
    { "layout": "closing", "title": "Questions", "subtitle": "Decide by 5 September" }
  ]
}
EOF
```

```bash
python3 {{SKILL_DIR}}/scripts/deck_pptx.py spec.json q3.pptx
```

Then deliver `q3.pptx`. Delivery runs the deck's validator,
`skill-pptx.validate_pptx`, on the exact file; if the deck is refused, the
refusal lists every problem by slide and shape. Fix the spec, render again and
deliver again — do not describe a refused build as a finished deck.

## Task: build a deck

```bash
python3 {{SKILL_DIR}}/scripts/deck_pptx.py <spec.json> <output.pptx> [--validate-only]
```

Run `--validate-only` first when the spec is generated programmatically: it
checks the whole spec, density limits included, and exits without writing
anything.

### The same deck as a PDF

```bash
python3 {{SKILL_DIR}}/scripts/deck_pdf.py <spec.json> <output.pdf>
```

One page per slide, same layouts, same palette, same type sizes — because it
reads the **spec**, not the `.pptx`. There is no PowerPoint converter in the
sandbox and there does not need to be one: two renderers over one spec cannot
disagree, and a spec that builds a deck builds a PDF.

Build both whenever both were asked for, and promote both:

```bash
python3 {{SKILL_DIR}}/scripts/deck_pptx.py spec.json out/q3.pptx
```

```bash
python3 {{SKILL_DIR}}/scripts/deck_pdf.py spec.json out/q3.pdf
```

Run each render as its own command: the interpreter, the script, the spec and
the output, with no shell wrapper and nothing chained before or after it. Do not
join the two renders, and do not chain a validator onto either of them — each
file is validated when it is delivered, and a render joined to other commands is
no longer a render that delivery can recognise. To check a file yourself, run
the validator as a separate command and rely on its own exit status.

Then deliver each file. The order matters only in that the spec is written once.
Do not build the deck, report it as done, and leave the PDF for a turn that never
comes — that is the single most common way this skill produces half a
deliverable.

### How the deck's PDF is checked

`deck_pdf.py` is a registered generator of this skill, like `deck_pptx.py`. A
PDF it renders from the spec is the requested PDF, not a substitute for one, and
it is delivered with the same production evidence as the deck. The PDF format
itself belongs to the PDF capability this plugin composes, so the PDF is checked
by that capability's validator, `skill-pdf.validate_pdf`, when it is delivered.
You do not need to load the `pdf` skill or run its validator yourself. If
delivery reports that the PDF failed validation, fix the spec and render it
again. `deck_pdf.py` states the same after every render.

`deck_pdf.py` draws text directly (`canvas.drawString`) with no Unicode Bidi
reordering and no Arabic contextual shaping, and neither is available to add
in the sandbox. It refuses a spec whose slide text is predominantly
right-to-left (Arabic, Hebrew) with a named `ERROR:` line rather than
rendering it disconnected and in the wrong order. `deck_pptx.py`'s `.pptx`
output is unaffected by this limit: PowerPoint, Keynote and Google Slides all
shape and reorder that text themselves when the file is opened. For a
right-to-left deliverable that must be a PDF, use the `pdf` skill directly
instead of `deck_pdf.py`.

Deck-level fields: `title` (required, also the metadata title) and `slides`
(required, 1–30). Optional: `subtitle` and `author` for the file metadata,
`theme` (`"light"` default, or `"dark"`), `aspect` (`"16:9"` default, or
`"4:3"`), `font`, and `palette`. With no explicit `font`, the runtime-owned
Chainabit default is `IBM Plex Sans`; an explicit safe family name overrides it.
`palette`, when supplied, is a complete override with `background`, `surface`,
`ink`, `body`, `muted`, `rule`, and `accent` values as `#RRGGBB`. It replaces the
selected default palette in full — it does not inherit Chainabit colours — so a
customer palette remains customer-branded.

### The eight layouts

| `layout`        | Fields                                             | For |
|-----------------|----------------------------------------------------|-----|
| `title`         | `title`, `subtitle?`, `meta?`                       | the cover |
| `content`       | `title`, `bullets` (1–6), `note?`                   | the body of the deck |
| `comparison`    | `title`, `left`/`right`, each `{heading, bullets}`  | two options, before/after |
| `closing`       | `title`, `subtitle?`, `contact?`                    | the last slide |
| `title-image`   | `title`, `image`, `subtitle?`, `meta?`              | a cover with a hero picture |
| `image-content` | `title`, `image`, `bullets` (1–6), `caption?`       | a picture beside the point it makes |
| `image-full`    | `title`, `image`, `caption?`                        | one picture, edge to edge |
| `chart`         | `title`, `chart` **or** `image`, `note?`            | numbers, plotted |

Any slide also takes `"notes"` — speaker notes, unlimited length, never rendered
on the slide. That is where the sentence goes when the bullet has to stay a
phrase.

There are no other layouts and no styling fields, on purpose. Size, colour and
position are decided from the content: each box takes the largest size on its
ladder that the text actually fits at, and when it will not fit even at the
floor, that is a spec error rather than type shrunk into illegibility.

### Pictures

```json
{ "layout": "image-content", "title": "Where the risk sits",
  "bullets": ["Payment review is open", "Crash dashboard is not wired"],
  "image": { "path": "path/the/copy/landed/at.png", "alt": "The release pipeline, three stages" },
  "caption": "Release pipeline, current state" }
```

Both fields are required. `path` is resolved **relative to the spec file's own
directory** and must stay inside it; `alt` is what a screen reader and
PowerPoint's own accessibility check read, so it describes the picture rather
than repeating the title.

A picture must already be a file before the spec names it. An image the platform
holds is copied into the workspace with the `workspace.files` tool: pass its
`sourceFileId` **on its own**, with no `path` and no `op`, and the result gives
the exact path the copy landed at. Use that path, verbatim, as `image.path`, and
write the spec where that path resolves from — the spec's directory is the root.

A link is not a picture. The renderers have no network, so a URL, a `data:` URI
or an absolute path in `image.path` is refused by name rather than drawn as a
blank. Accepted formats are PNG, JPEG and GIF, up to 25 MB and 40 megapixels.

`title-image` and `image-full` crop the picture to fill their box; `image-content`
and a `chart` slide's picture are letterboxed, because cropping a diagram
silently removes part of the figure.

### Charts

```json
{ "layout": "chart", "title": "Open items by priority",
  "chart": { "kind": "bar", "categories": ["P1", "P2", "P3"],
             "series": [{ "name": "Open",   "values": [3, 7, 4] },
                        { "name": "Closed", "values": [1, 5, 9] }],
             "unit": "items" },
  "note": "Counted at 09:00 today." }
```

`kind` is `bar`, `line` or `pie`. Every series carries one value per category.
Up to 12 categories and 4 series; a `pie` takes exactly one series, because a pie
shows one whole. `unit` is optional and labels the value axis.

This is a **real chart part** in the delivered file: whoever opens it can correct
a number, recolour a series or re-point the data. That is why it is preferred
over a picture of a chart. Every value is printed on the chart, and a legend
appears as soon as more than one thing is being distinguished — colour alone is
not a label.

When the figure already exists as a picture, a `chart` slide takes `image`
instead of `chart`. It takes one or the other, never both.

### A slide cannot promise a figure it does not carry

`image` and `chart` are **required** by the layouts that declare them, and the
spec is refused when any text field reads as a stand-in for a figure —
`(Chart inserted here…)`, `[image goes here]`, `TODO`, `TBD`, `lorem ipsum`.
There is no spelling of these slides that renders without the picture or the
data, so a deck asked for with a figure in it either has one or is refused. If
the figure is not available yet, leave the slide out and say so; do not describe
it on a slide.

### Error output

Every problem is reported in one run, addressed by path:

```
ERROR: slides[1].bullets: 7 bullets, the limit is 6. Split this slide in two, or move the extras into 'notes'.
ERROR: slides[2].bullets[0]: 28 words, the limit is 20. A bullet is a cue; put the sentence in 'notes' and leave a phrase here.
ERROR: slides[3].title: will not fit its box at the 24pt floor. Shorten the slide title.
```

Correct all of them at once and re-run. Do not fix one and retry. Density errors
are not style advice — the fix is always to cut text or add a slide, never to
make the type smaller.

## Task: check an existing deck

```bash
python3 {{SKILL_DIR}}/scripts/validate_pptx.py <file.pptx> [--strict]
```

Use this for a `.pptx` you did not just build here — one the user supplied, or
one another tool produced. A deck this skill builds is checked by the same
validator when it is delivered, so building one needs no separate check.

Per slide, it reports empty slides; text overflowing its box or running off the
slide edge, with the estimated and available sizes; the computed WCAG contrast
ratio of each run against the colour actually behind it, resolved through theme
colours and backgrounds; type under the 14pt floor (and under 18pt for body); and
bullets, words per bullet, and slides over the density limits.

It also counts what the deck embeds — pictures and chart parts, per slide and in
total — on stdout and in the `subject` of its JSON frame. That is not a defect
check: a deck of clean, readable text passes every other check whether or not it
carries the figure it was asked for, and the count is what separates the two.

Exit codes:

- `0` — the deck is deliverable. `WARNING:` lines may still appear; read them.
- `1` — do not hand the file to the user. Every `ERROR:` line names the slide,
  the shape, and the change to make.

`--strict` promotes warnings to failures. `--min-font`, `--max-bullets`,
`--max-words` and `--max-slides` move the limits when a deck genuinely needs a
different one; move them deliberately, not to silence a finding.

The check is stdlib-only and reads the file's own OOXML, so it works on decks
this skill did not produce — PowerPoint, Keynote, or Google Slides alike.

## Working pattern

1. **Plan.** Decide the sequence first, as titles only. If a title cannot be
   stated in a phrase, the slide is doing two jobs. At the same time, note every
   format the request asked for — a deck, a PDF of it, a data file beside it.
2. **Check the spec.** Write the spec, run `--validate-only`, fix every
   `ERROR:` line.
3. **Render.** Build **every** format noted in step 1, not just the first, each
   with its own command.
4. **Deliver.** Promote every file built in step 3. Delivery validates the deck
   with `skill-pptx.validate_pptx` and its PDF with `skill-pdf.validate_pdf`; if
   either is refused, fix the spec, render that file again and deliver it again.
   A file sitting in the workspace has not been handed to anyone.

## Reference

`references/design.md` — contrast minimums with their measured ratios, the
palette for both themes, the font-size ladders and why the floors sit where they
do, the density limits, and the slide geometry. Read it before overriding a
colour, a size, or a limit.

## Script quick reference

| Task                                   | Command |
|----------------------------------------|---------|
| Build a deck from a spec                | `python3 {{SKILL_DIR}}/scripts/deck_pptx.py spec.json out.pptx` |
| Render the same spec as a PDF           | `python3 {{SKILL_DIR}}/scripts/deck_pdf.py spec.json out.pdf` |
| Check a spec before building            | `python3 {{SKILL_DIR}}/scripts/deck_pptx.py spec.json out.pptx --validate-only` |
| Check a deck you did not build here     | `python3 {{SKILL_DIR}}/scripts/validate_pptx.py file.pptx` |

All three scripts accept `--help`.

**Exit codes are a contract, not a crash.** `deck_pptx.py`, `deck_pdf.py` and
`validate_pptx.py` all exit `1` with `ERROR:` lines when the input needs
fixing. That is the script working, not the script failing: read the lines, fix
every one of them, re-run. Only an exception traceback means something actually
broke.
