<!-- SPDX-License-Identifier: Apache-2.0 -->

# Web layout, typography and colour

Read this before overriding a colour, before choosing a font size, and before
adding a breakpoint. Every number here is either emitted by `scripts/scaffold_site.py`
or enforced by `scripts/validate_site.py`; the documents and the code are one
decision written twice, so if you change one, change the other.

A web page is not a slide and not a page of print. It is read at arm's length on
a screen whose width the author does not control, at a text size the reader may
have overridden, possibly with the system set to dark. Every constraint below
follows from one of those four facts.

## Contrast

The ratios are WCAG 2.1 relative luminance, the same formula `scaffold_site.py`
computes when it checks an accent.

| Text | WCAG AA minimum | Enforced as |
|------|-----------------|-------------|
| Normal — under 18pt / 24px | **4.5:1** | error below it |
| Large — 18pt / 24px and up, or 14pt bold | **3:1** | — |
| Accent, whatever its size | **4.5:1** | error below it |
| Non-text (borders, dividers) | 3:1 | not enforced; these carry no information here |

The accent is held to the normal-text floor even though most accent text is a
heading or a button label, because the same token also colours links inside body
copy. A value chosen against a 3:1 heading floor becomes an unreadable link two
paragraphs later.

Two consequences worth stating plainly:

- **Never put body text over a photograph.** The ratio is different in every
  square centimetre and cannot be computed. Put the text on a solid panel.
- **Mid-grey is the usual offender.** `#9CA3AF` on white is 2.54:1 — it looks
  refined in a mockup and is illegible to a large minority of readers. Use the
  muted values below, which are the lightest greys that still clear AA.

## Palette

Both themes are complete and internally checked. Each text value is listed with
its measured ratio against the surface it actually sits on.

### Light

| Role | Hex | On | Ratio |
|------|-----|----|-------|
| Background | `#FFFFFF` | — | — |
| Surface (cards) | `#F8FAFC` | — | — |
| Ink (headings) | `#0F172A` | background | 17.85:1 |
| Body | `#1E293B` | background | 14.63:1 |
| Muted (meta, footer) | `#475569` | background | 7.58:1 |
| Muted on a card | `#475569` | surface | 7.24:1 |
| Rule / divider | `#CBD5E1` | — | non-text |
| Accent | `#1D4ED8` | background | 6.70:1 |
| Accent on a card | `#1D4ED8` | surface | 6.41:1 |
| Button label | `#FFFFFF` | accent | 6.70:1 |

### Dark

| Role | Hex | On | Ratio |
|------|-----|----|-------|
| Background | `#0F172A` | — | — |
| Surface (cards) | `#1E293B` | — | — |
| Ink (headings) | `#F8FAFC` | background | 17.06:1 |
| Body | `#E2E8F0` | background | 14.48:1 |
| Muted | `#94A3B8` | background | 6.96:1 |
| Muted on a card | `#94A3B8` | surface | 5.71:1 |
| Rule / divider | `#334155` | — | non-text |
| Accent | `#60A5FA` | background | 7.02:1 |
| Accent on a card | `#60A5FA` | surface | 5.75:1 |
| Button label | `#0F172A` | accent | 7.02:1 |

### Semantic accents, if a status or a chart needs one

All clear 4.5:1 against their own background, so each can carry text as well as
fill a shape.

| Meaning | On light | Ratio | On dark | Ratio |
|---------|----------|-------|---------|-------|
| Positive | `#047857` | 5.48:1 | `#6EE7B7` | 11.71:1 |
| Caution | `#B45309` | 5.02:1 | `#FCD34D` | 12.38:1 |
| Negative | `#BE123C` | 6.29:1 | `#FDA4AF` | 9.44:1 |
| Second series | `#0F766E` | 5.47:1 | `#5EEAD4` | 12.07:1 |

Never encode meaning in colour alone — roughly one man in twelve cannot separate
the positive from the negative pair. Label the value, or change the shape too.

### Changing the accent

`"accent"` in the spec sets the light-theme accent and `"accentDark"` the dark
one. Both are checked against their own background and the generator refuses a
value under 4.5:1, quoting the measured ratio. That is a hard error rather than a
warning because an accent is the one colour that appears on every page.

## Type scale

Sizes are fluid: a **1.200** ratio at a 320px viewport opening to **1.250** at
1280px, expressed as `clamp()` so the value is capped at both ends rather than
growing without limit on an ultrawide monitor.

| Token | 320px | 1280px | Used for |
|-------|-------|--------|----------|
| `--step-5` | 39.8px | 51.9px | `h1` |
| `--step-4` | 33.2px | 41.5px | spare display size |
| `--step-3` | 27.6px | 33.2px | `h2` |
| `--step-2` | 23.0px | 26.6px | spare |
| `--step-1` | 19.2px | 21.2px | `h3`, hero lede |
| `--step-0` | 16.0px | 17.0px | body |
| `--step--1` | 13.3px | 13.6px | meta, footer |

Rules that hold regardless of the scale:

- **16px is the body floor.** Below it iOS Safari zooms on focus and every
  reader over forty is squinting. The scale never goes under it for body copy.
- **Line height 1.6 for body, 1.2 for headings.** Long lines need more leading;
  display type needs less or it falls apart into separate lines.
- **`--measure: 68ch` caps paragraph width.** Past roughly 75 characters the eye
  loses the start of the next line. This is why `p` has a `max-width` and the
  container does not.
- Never set a size in `px` directly. A reader who has raised their browser's
  default text size is telling you something, and `rem` respects it.

## Spacing rhythm

A 4px base, exposed as nine tokens. Every margin, padding and gap in the
generated stylesheet is one of these — that is what stops a page drifting into
13px here and 27px there.

| Token | Value | Typical use |
|-------|-------|-------------|
| `--space-1` | 0.25rem / 4px | tight label gaps |
| `--space-2` | 0.5rem / 8px | inline gaps |
| `--space-3` | 0.75rem / 12px | button padding (vertical) |
| `--space-4` | 1rem / 16px | paragraph margin |
| `--space-5` | 1.5rem / 24px | grid gap, container padding |
| `--space-6` | 2rem / 32px | wide container padding |
| `--space-7` | 3rem / 48px | section padding |
| `--space-8` | 4rem / 64px | hero padding |
| `--space-9` | 6rem / 96px | hero padding, wide viewport |

Vertical rhythm comes from the section: every `section` carries `--space-7` top
and bottom and a hairline rule, so the spacing between two blocks of content is
decided once rather than per block.

## Breakpoints

Three, mobile-first, in `rem` so they scale with the reader's text size.

| Breakpoint | Width | What changes |
|------------|-------|--------------|
| base | under 40rem / 640px | single column, nav wraps under the brand |
| `40rem` | 640px | card grids go to two columns |
| `60rem` | 960px | card grids go to three, hero gains vertical space, containers widen |

`--content: 72rem` caps the container so a line of text never runs the full width
of a 27-inch monitor.

These are **content breakpoints**, not device breakpoints. Each one is the width
at which this particular layout stops working — three cards at 200px each is the
point where a card stops holding a sentence — not the width of any phone. Adding
a breakpoint because "tablet" exists is how a stylesheet acquires six of them and
a bug at every seam.

Two things the stylesheet does that are easy to drop and expensive to lose:

- `<meta name="viewport" content="width=device-width, initial-scale=1">` on every
  page. Without it a phone lays the document out at 980px and scales the result
  down, so every query above is answered against a viewport nobody has.
  `validate_site.py` fails a page that is missing it.
- `@media (prefers-reduced-motion: reduce)` collapsing animation durations. There
  is no animation in the templates today, but a starting point that omits it
  teaches the next author the wrong habit.

## Fonts

Local, runtime-provided webfonts only. Nothing is fetched, so nothing can fail
to arrive and there is no flash of invisible text.

```
--font-sans: "IBM Plex Sans", "IBM Plex Sans Arabic", sans-serif;
--font-mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas,
             "Liberation Mono", monospace;
```

The generated site carries local IBM Plex Sans and IBM Plex Sans Arabic WOFF2
assets. Latin Extended-A — `ç ş ğ ı İ ö ü` — therefore renders offline; Arabic
uses the explicit script-compatible companion family.

A webfont is not an option here, and not only as a matter of taste: the sandbox
has no network egress, so `@import` from Google Fonts resolves to nothing and the
page silently renders in the fallback face you did not choose. `validate_site.py`
treats any remote asset reference as an error for exactly that reason. If a
specific face is genuinely required, the font file has to be copied into the site
directory and referenced relatively, or inlined as a `data:` URI.

## Accessibility floor

The generated pages carry these, and the validator checks most of them. They are
the difference between a site that works and one that merely renders.

- One `<h1>` per page, from the hero. Headings descend without skipping levels.
- A visible focus ring on every interactive element, at 3px in the accent colour
  with a 2px offset. Removing the outline without replacing it is the single most
  common way a hand-written site becomes unusable by keyboard.
- A skip link as the first focusable element, jumping to `#main`.
- `aria-current="page"` on the nav link for the current page — the only thing
  that tells a screen-reader user which one they are already on.
- `alt` on every `<img>`. Descriptive when the image carries meaning, `alt=""`
  when it is decorative, but present either way.
- `lang` on `<html>`, so assistive technology knows how to pronounce the page.
