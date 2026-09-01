---
name: pptx
description: Builds real .pptx presentations inside the sandbox from a JSON spec, using four brand-safe layouts with a checked palette and type scale, renders that same spec as a matching PDF, and verifies the result is presentable before it is handed back. Use when the requested deliverable is a slide deck: the request mentions PowerPoint, pptx, .pptx, slides, a deck, a presentation, a pitch, "sunum", "slayt", a board update or a talk - including when it also asks for a PDF of that deck, or for the deck in several formats at once. Also use to check whether an existing .pptx is valid, or whether its slides are empty, overflowing, unreadably small, or too dense. Do NOT use when the deliverable is a document or report meant to be read rather than shown - use the pdf skill; do NOT use for a spreadsheet (.xlsx), a Markdown outline, or a web page. Renders Turkish and other Latin Extended-A characters correctly.
license: Apache-2.0
metadata:
  version: 1.2.0
---

# Presentation generation

## Overview

Three scripts, one spec. Two of them render that spec into a format; the third
proves the result can be read from the back of a room.

- **`deck_pptx.py`** — JSON spec in, `.pptx` out. You supply content; the script
  decides layout, palette and type size. There is no per-slide styling knob,
  because that is where bad decks come from.
- **`deck_pdf.py`** — the same spec in, `.pdf` out, one page per slide. Not a
  converter: it reads the spec, not the `.pptx`, and shares the other script's
  layout engine, so the two outputs agree by construction instead of by
  inspection. This is what a request for "the deck and a PDF" needs.
- **`validate_pptx.py`** — the exit gate. Catches the defects that raise no
  exception: empty slides, text spilling out of its box, contrast below the
  readable floor, type under the size floor, over-dense bullet lists.

All script paths below are **relative to this skill's own directory**. If the
skill is materialised at `/workspace/.skills/pptx/`, then `scripts/deck_pptx.py`
means `/workspace/.skills/pptx/scripts/deck_pptx.py`. The scripts write only to
the output path they are given.

`python-pptx` and `reportlab` are **already installed** in the sandbox image, so
both scripts run with no setup step. Do not guess at what else is installed or
whether you may install it — that is not a fixed property of this skill, it is a
property of the container and the lease's policy, and it changes. Call the
`workspace.env` tool: it reports the interpreters, the installed packages, the
binaries on `PATH`, and whether installing more is permitted right now. Nothing
else in this document makes a claim about the environment.

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
cat > /workspace/spec.json <<'EOF'
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

python3 scripts/deck_pptx.py /workspace/spec.json /workspace/q3.pptx
python3 scripts/validate_pptx.py /workspace/q3.pptx
```

Expected output from the second command:

```
OK: /workspace/q3.pptx is a .pptx presentation, 37577 bytes, 4 slide(s), 13.33x7.50 in (16:9)
  slide 1: 3 text block(s), min font 18pt, min contrast 7.6:1
```

If validation prints `ERROR:` lines, the file is not deliverable. Fix the cause
and re-run — do not describe a failed build as a finished deck.

## Task: build a deck

```bash
python3 scripts/deck_pptx.py <spec.json> <output.pptx> [--validate-only]
```

Run `--validate-only` first when the spec is generated programmatically: it
checks the whole spec, density limits included, and exits without writing
anything.

### The same deck as a PDF

```bash
python3 scripts/deck_pdf.py <spec.json> <output.pdf>
```

One page per slide, same layouts, same palette, same type sizes — because it
reads the **spec**, not the `.pptx`. There is no PowerPoint converter in the
sandbox and there does not need to be one: two renderers over one spec cannot
disagree, and a spec that builds a deck builds a PDF.

Build both whenever both were asked for, and promote both:

```bash
python3 scripts/deck_pptx.py spec.json /workspace/out/q3.pptx
python3 scripts/deck_pdf.py  spec.json /workspace/out/q3.pdf
python3 scripts/validate_pptx.py /workspace/out/q3.pptx
```

The order matters only in that the spec is written once. Do not build the deck,
report it as done, and leave the PDF for a turn that never comes — that is the
single most common way this skill produces half a deliverable.

Deck-level fields: `title` (required, also the metadata title) and `slides`
(required, 1–30). Optional: `subtitle` and `author` for the file metadata,
`theme` (`"light"` default, or `"dark"`), `aspect` (`"16:9"` default, or
`"4:3"`), and `font`. With no explicit `font`, the runtime-owned Chainabit
default is `IBM Plex Sans`; an explicit safe family name overrides it.

### The four layouts

| `layout`     | Fields                                             | For |
|--------------|----------------------------------------------------|-----|
| `title`      | `title`, `subtitle?`, `meta?`                       | the cover |
| `content`    | `title`, `bullets` (1–6), `note?`                   | the body of the deck |
| `comparison` | `title`, `left`/`right`, each `{heading, bullets}`  | two options, before/after |
| `closing`    | `title`, `subtitle?`, `contact?`                    | the last slide |

Any slide also takes `"notes"` — speaker notes, unlimited length, never rendered
on the slide. That is where the sentence goes when the bullet has to stay a
phrase.

There are no other layouts and no styling fields, on purpose. Size, colour and
position are decided from the content: each box takes the largest size on its
ladder that the text actually fits at, and when it will not fit even at the
floor, that is a spec error rather than type shrunk into illegibility.

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

## Task: validate a deck

```bash
python3 scripts/validate_pptx.py <file.pptx> [--strict]
```

Per slide, it reports empty slides; text overflowing its box or running off the
slide edge, with the estimated and available sizes; the computed WCAG contrast
ratio of each run against the colour actually behind it, resolved through theme
colours and backgrounds; type under the 14pt floor (and under 18pt for body); and
bullets, words per bullet, and slides over the density limits.

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
2. **Validate.** Write the spec, run `--validate-only`, fix every `ERROR:` line.
3. **Execute.** Build **every** format noted in step 1, not just the first.
4. **Verify.** Run `validate_pptx.py`. Only after it exits 0 is there a deck to
   talk about.
5. **Deliver.** Promote every file built in step 3. A file sitting in the
   workspace has not been handed to anyone.

## Reference

`references/design.md` — contrast minimums with their measured ratios, the
palette for both themes, the font-size ladders and why the floors sit where they
do, the density limits, and the slide geometry. Read it before overriding a
colour, a size, or a limit.

## Script quick reference

| Task                                   | Command |
|----------------------------------------|---------|
| Build a deck from a spec                | `python3 scripts/deck_pptx.py spec.json out.pptx` |
| Render the same spec as a PDF           | `python3 scripts/deck_pdf.py spec.json out.pdf` |
| Check a spec before building            | `python3 scripts/deck_pptx.py spec.json out.pptx --validate-only` |
| Confirm a deck is presentable           | `python3 scripts/validate_pptx.py out.pptx` |

All three scripts accept `--help`.

**Exit codes are a contract, not a crash.** `deck_pptx.py`, `deck_pdf.py` and
`validate_pptx.py` all exit `1` with `ERROR:` lines when the input needs
fixing. That is the script working, not the script failing: read the lines, fix
every one of them, re-run. Only an exception traceback means something actually
broke.
