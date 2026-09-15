<!-- SPDX-License-Identifier: Apache-2.0 -->

# Slide typography, colour and density

Read this before overriding a palette, before choosing a font size, and before
deciding how much text a slide can hold. Every number here is enforced by
`scripts/validate_pptx.py`; the two are one decision written twice, so if you
change one, change the other.

A slide is not a page. It is read at four metres, off a projector that has lost a
third of its contrast to ambient light, by someone who is also listening to a
person talk. That is the whole reason the floors below are stricter than they
would be for print.

## Contrast

The ratios are WCAG 2.1 relative luminance, the same formula the validator
computes:

| Text | WCAG AA minimum | Enforced as |
|------|-----------------|-------------|
| Normal — under 18pt, or under 14pt bold | **4.5:1** | error below it |
| Large — 18pt and up, or 14pt bold and up | **3:1** | error below it |
| Anything, on a projector | **4.5:1** | warning between 3:1 and 4.5:1 |

Almost all slide text is "large" by the WCAG definition, which would let a 3:1
pairing through. It should not go through. Projection loses contrast that a
monitor keeps, so 4.5:1 is the floor this skill targets for every run of text and
3:1 is the point below which the deck is simply rejected.

Two consequences worth stating plainly:

- **Never put body text on a photograph.** The ratio is different in every square
  centimetre and cannot be checked. Put the text on a solid panel over the image.
  The validator says `contrast cannot be computed` when it finds this, which is a
  fact, not a pass.
- **Mid-grey is the usual offender.** `#858585` on white is 3.69:1 — it looks
  refined on a laptop and disappears in a room. Use the muted values below.

## Palette

Both themes are complete and internally checked. Each accent and text value is
listed with its measured ratio against the surface it is meant to sit on.

### Light

| Role | Hex | On | Ratio |
|------|-----|----|-------|
| Background | `#FFFFFF` | — | — |
| Surface (panels) | `#F9FAFB` | — | — |
| Ink (titles) | `#101828` | white | 17.75:1 |
| Body | `#364153` | white | 10.30:1 |
| Muted (meta, footnotes) | `#6A7282` | white | 4.84:1 |
| Rule / divider | `#E5E7EB` | — | non-text |
| Accent | `#327B61` | surface | 4.90:1 |

### Dark

| Role | Hex | On | Ratio |
|------|-----|----|-------|
| Background | `#010102` | — | — |
| Surface (panels) | `#18181B` | — | — |
| Ink (titles) | `#D7D7DA` | background | 14.53:1 |
| Body | `#A4A4A9` | background | 8.41:1 |
| Muted | `#85858D` | background | 5.70:1 |
| Rule / divider | `#1F1F22` | — | non-text |
| Accent | `#70BD9E` | surface | 8.80:1 |

### Semantic accents, if a chart or a status needs one

All are quoted against white and all clear 4.5:1, so they can carry text as well
as fill a bar. On a dark background use the lighter partner instead.

| Meaning | On light | Ratio | On dark |
|---------|----------|-------|---------|
| Positive | `#047857` | 5.48:1 | `#6EE7B7` (11.71:1) |
| Caution | `#B45309` | 5.02:1 | `#FCD34D` (12.38:1) |
| Negative | `#BE123C` | 6.29:1 | `#FDA4AF` (9.44:1) |
| Neutral second series | `#0F766E` | 5.47:1 | `#5EEAD4` (12.07:1) |

Never encode meaning in colour alone — roughly one man in twelve cannot separate
the positive from the negative pair. Label the value, or change the shape too.

## Font sizes

Points on a slide are not points on a page: a 13.33in-wide slide projected to
three metres magnifies roughly nine times, and a 10pt footnote lands at the
apparent size of 6pt print seen across a desk.

| Role | Size | Rule |
|------|------|------|
| Cover title | 44 / 38 / 32pt | steps down only if the title is long |
| Slide title | 32 / 28 / 24pt | 24pt is the floor for a title |
| Body, bullets | 22 / 20 / 18pt | **18pt is the body floor** |
| Two-column bullets | 20 / 18pt | half the width holds less |
| Meta, footnote, contact | 18pt | one line, never smaller |
| **Absolute floor, any text** | **14pt** | below this the validator fails the deck |

The ladders are deliberate. A layout that shrinks type to fit more content has
answered the wrong question; the size steps down at most twice, and when the text
still does not fit, the content is too long and the tool says so instead.

14pt is the hard floor because it is roughly the smallest size legible from the
back of a 10-metre room at this magnification. Below it, a source line or an axis
label is decoration.

## Density

| Limit | Value | Enforced as |
|-------|-------|-------------|
| Bullets per slide | **6** | error above it |
| Words per bullet | **12** target, 20 hard | warning over 12, error over 20 |
| Slides per deck | **30** | error above it, raise with `--max-slides` |
| Lines per bullet | 2 | implied by the box height; overflow is an error |

Six bullets of twelve words is about 70 words on a slide, which is roughly the
most a person can read while also listening. Beyond that the audience reads and
stops listening, which is worse than either alone.

The correct home for the full sentence is the speaker notes — `"notes"` on any
slide in the spec. Notes are unlimited, they never render on the slide, and they
are what the presenter actually says. Moving text there is not losing it.

## Geometry

`deck_pptx.py` lays out a 13.333 × 7.5in slide (16:9; `"aspect": "4:3"` gives
10 × 7.5in) with a 0.85in margin, so the live area is 11.63in wide.

- Titles sit in a band at 0.6in from the top, over a hairline rule at 1.5in.
- Body starts at 1.8in and runs to 1.05in from the bottom.
- Comparison columns split the live area with a 0.4in gutter and sit on the
  surface colour so each half reads as one panel.
- A hero picture takes the right 45% of the cover, full height; the cover text
  keeps the left, one gutter clear of it.
- `image-content` puts the picture in the left column and the bullets in the
  right, on the same grid as a comparison slide, with the caption under the
  picture.
- `image-full` runs the picture edge to edge and sets the title in a **solid**
  band 1.0in deep across the foot. The band is solid rather than translucent
  because text over a picture has no computable contrast ratio, and "check it by
  eye" is not a floor.
- A chart takes the body box, so a chart slide and a bullet slide sit on the
  same grid.

## Pictures and charts

| Limit | Value | Enforced as |
|-------|-------|-------------|
| Picture formats | PNG, JPEG, GIF | error on anything else |
| Picture size | **25 MB**, **40 megapixels** | error above either |
| Picture location | inside the spec file's own directory | error outside it |
| Chart categories | **12** | error above it |
| Chart series | **4**, and exactly **1** for a pie | error above it |
| Chart text | **16pt** | above the 14pt absolute floor |

The format list is the intersection of what the pdf capability accepts and what
an OOXML package can carry, so one picture is either safe to embed in a deck and
the report beside it, or in neither. The size and pixel ceilings are the pdf
capability's own numbers, kept identical on purpose.

A picture is never stretched. `title-image` and `image-full` crop to fill their
box; `image-content` and a chart slide's picture are letterboxed, because
cropping a diagram silently removes part of the figure.

Native charts use the deck's accent first, then the semantic set above, and print
every value on the chart. A legend appears as soon as more than one thing is
being distinguished. Colour alone never carries the meaning — see the note under
the semantic accents.

Text boxes are written with autofit **off**. python-pptx's default is to turn it
on, which lets a box silently resize itself around whatever it is handed — after
which the geometry above no longer describes the slide and nothing can be checked
against it. Off means the declared box is the real box, and overflow is a defect
the validator can see.

## Fonts

`IBM Plex Sans` is the Chainabit default. The prepared runtime provides its
metrics deterministically, and the generator declares it in the OOXML theme,
default text styles and explicit runs. An explicit safe `"font"` in the spec
takes precedence. The validator rejects inconsistent concrete families so an
implicit office-suite default cannot silently re-enter the package.
