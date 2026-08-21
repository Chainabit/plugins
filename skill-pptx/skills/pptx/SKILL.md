---
name: pptx
description: Builds real .pptx presentations inside the sandbox from a JSON spec, using four brand-safe layouts with a checked palette and type scale, and verifies the result is presentable before it is handed back. Use when the requested deliverable is a slide deck: the request mentions PowerPoint, pptx, .pptx, slides, a deck, a presentation, a pitch, "sunum", "slayt", a board update or a talk. Also use to check whether an existing .pptx is valid, or whether its slides are empty, overflowing, unreadably small, or too dense. Do NOT use when the deliverable is a document or report meant to be read rather than shown - use the pdf skill; do NOT use for a spreadsheet (.xlsx), a Markdown outline, or a web page. Renders Turkish and other Latin Extended-A characters correctly.
license: Apache-2.0
metadata:
  version: 1.0.0
---

# Presentation generation

## Overview

Two scripts. One builds a deck from a JSON spec using fixed layouts, the other
proves the result can be read from the back of a room.

- **`deck_pptx.py`** — JSON spec in, `.pptx` out. You supply content; the script
  decides layout, palette and type size. There is no per-slide styling knob,
  because that is where bad decks come from.
- **`validate_pptx.py`** — the exit gate. Catches the defects that raise no
  exception: empty slides, text spilling out of its box, contrast below the
  readable floor, type under the size floor, over-dense bullet lists.

All script paths below are **relative to this skill's own directory**. If the
skill is materialised at `/workspace/.skills/pptx/`, then `scripts/deck_pptx.py`
means `/workspace/.skills/pptx/scripts/deck_pptx.py`. The scripts write only to
the output path they are given.

`python-pptx` is **already installed** in the sandbox image, and there is no
network at runtime. Do not run `pip install`, `uv pip install`, or `apt-get`. If
an import error appears, the script says so explicitly; that means the
environment is wrong, not that a dependency is missing.

A deck is for talking over. If the deliverable is meant to be *read* — a report,
a memo, a one-pager — build a PDF instead. A document rendered as slides is the
worst artefact of the pair: too shallow to read, too dense to show.

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

Deck-level fields: `title` (required, also the metadata title) and `slides`
(required, 1–30). Optional: `subtitle` and `author` for the file metadata,
`theme` (`"light"` default, or `"dark"`), `aspect` (`"16:9"` default, or
`"4:3"`), and `font` (default `Arial` — read `references/design.md` first).

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
   stated in a phrase, the slide is doing two jobs.
2. **Validate.** Write the spec, run `--validate-only`, fix every `ERROR:` line.
3. **Execute.** Build the deck.
4. **Verify.** Run `validate_pptx.py`. Only after it exits 0 is there a deck to
   talk about.

## Reference

`references/design.md` — contrast minimums with their measured ratios, the
palette for both themes, the font-size ladders and why the floors sit where they
do, the density limits, and the slide geometry. Read it before overriding a
colour, a size, or a limit.

## Script quick reference

| Task                                   | Command |
|----------------------------------------|---------|
| Build a deck from a spec                | `python3 scripts/deck_pptx.py spec.json out.pptx` |
| Check a spec before building            | `python3 scripts/deck_pptx.py spec.json out.pptx --validate-only` |
| Confirm a deck is presentable           | `python3 scripts/validate_pptx.py out.pptx` |

Both scripts accept `--help`.
