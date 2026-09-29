"""What a Markdown source file may contain before it is Markdown at all.

A delivered book opened on ``---\\ntitle: "..."\\nauthor: "..."`` printed as
characters, every heading and paragraph break after it printed as ``\\n\\n#``,
and thirty-one pages were filled by recycling forty-three sentences. All three
were accepted as valid Markdown, because they are valid Markdown: a backslash
before ``n`` is a backslash and a letter, a front-matter block is a rule and a
line of text, and a repeated sentence is a sentence. The file was well formed
and the document was wrong.

This module is the one place that knows those three shapes. It looks at the
source before layout, names every problem at once so a single revision fixes
them, and never rewrites a character of the source: what is refused is refused,
not repaired.

Nothing here is a judgement about wording. A finding is a structural fact about
the file: an escape sequence outside code, a leading metadata block, or a
sentence that appears word for word earlier in the same document.
"""
from __future__ import annotations

import re
from typing import Iterator

from .errors import ErrorCode, PdfError

# A byte order mark a text editor may have put in front of the first line.
_BOM = chr(0xFEFF)

_FENCE = re.compile(r"^[ ]{0,3}(?P<mark>`{3,}|~{3,})(?P<rest>.*)$")
_CODE_SPAN = re.compile(r"(`+)(?:(?!\1).)+?\1")

# Two characters, a backslash and an ``n``, standing for a line break, or the
# four of an escaped ``\r\n``. TeX never has a command that is only ``\n`` --
# ``\nabla``, ``\neq``, ``\nu`` and every other command starting with n continue
# with a letter -- and ``\\n`` is a TeX row break followed by a variable, so
# neither shape is an escaped line break. The price of that safety is a lone
# ``\n`` directly before an ASCII letter, which cannot be told from a TeX command;
# a source written that way has other breaks that are found.
_ESCAPED_BREAK = re.compile(r"(?<!\\)(?:\\r\\n|\\n(?![A-Za-z]))")

_FRONT_MATTER_KEY = re.compile(r"^[A-Za-z_][\w-]*[ ]*:(?:[ ].*)?$")
_ESCAPED_FRONT_MATTER = re.compile(r'^---\\n[ ]*[A-Za-z_][\w-]*[ ]*:')

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_NOT_WORD = re.compile(r"[\W_]+")

# A document that talks about escapes ("strip control characters except \t \n \r")
# has one or two of them among lines it did break. One written with them has three
# or more, or more of them than real line breaks.
MIN_ESCAPED_BREAKS = 3

# A sentence shorter than this is a heading, a label or a table cell, all of
# which legitimately recur.
MIN_SENTENCE_WORDS = 5
# Padding reads as most of the document repeating itself: the delivered book
# repeated 97% of 1,612 sentences. A reference whose every endpoint restates its
# access rule, a contract's recurring clause, or a refrain stays well under both
# (the most repetitive real reference measured was 53%).
MIN_REPEATED_SENTENCES = 50
MAX_REPEATED_SHARE = 0.6


def _prose_lines(text: str, *, skip_indented: bool = False) -> Iterator[tuple[int, str]]:
    """Every line outside a fenced code block, code spans removed, with its number.

    ``skip_indented`` also drops lines indented four columns or more: an
    indented code block, or a list item's continuation, which can quote ``\\n``
    legitimately and which no escaped source is written in.
    """
    fence: tuple[str, int] | None = None
    for number, raw in enumerate(text.split("\n"), 1):
        line = raw.rstrip("\r")
        opened = _FENCE.match(line)
        if fence:
            if (
                opened
                and opened.group("mark")[0] == fence[0]
                and len(opened.group("mark")) >= fence[1]
                and not opened.group("rest").strip()
            ):
                fence = None
            continue
        if opened and not (opened.group("mark")[0] == "`" and "`" in opened.group("rest")):
            fence = (opened.group("mark")[0], len(opened.group("mark")))
            continue
        if skip_indented and (line.startswith("    ") or line.startswith("\t")):
            continue
        yield number, _CODE_SPAN.sub("", line)


def escaped_line_breaks(text: str) -> tuple[int, int] | None:
    """The first line and the count of ``\\n`` escapes, if the source is written with them.

    Escapes inside code spans, fenced blocks and indented lines are code, not
    line breaks. What is left is an escaped source when it holds
    ``MIN_ESCAPED_BREAKS`` or more of them, or more of them than the source has
    real line breaks (a short document written on one line).
    """
    first, count = 0, 0
    for number, line in _prose_lines(text, skip_indented=True):
        found = len(_ESCAPED_BREAK.findall(line))
        if found:
            first, count = first or number, count + found
    if count >= MIN_ESCAPED_BREAKS or count > text.count("\n"):
        return first, count
    return None


def front_matter_end(text: str) -> int | None:
    """The last line of a leading ``---`` metadata block, if the source opens with one.

    The block must be what front matter is: an opening rule on the first line,
    then ``key: value`` lines (or the indented and dashed lines of a YAML list),
    then a closing rule. A document that opens with a rule and then prose is
    not front matter, and is left alone. A block whose line breaks were escaped
    sits on line one.
    """
    if _ESCAPED_FRONT_MATTER.match(text.lstrip(_BOM)):
        return 1
    lines = [line.rstrip() for line in text.lstrip(_BOM).split("\n")]
    if not lines or lines[0] != "---":
        return None
    keys = 0
    for index, line in enumerate(lines[1:], 2):
        if line in ("---", "..."):
            return index if keys else None
        if _FRONT_MATTER_KEY.match(line):
            keys += 1
        elif line and not line.startswith((" ", "\t", "- ")):
            return None
    return None


def repeated_sentences(text: str) -> tuple[int, int]:
    """How many prose sentences repeat an earlier one word for word, of how many.

    Sentences are read from paragraphs (a wrapped line is not a sentence break)
    outside code, and compared after case folding and dropping punctuation, so
    a recycled sentence is found however it was wrapped or capitalised. Table
    rows and sentences under ``MIN_SENTENCE_WORDS`` words are not counted.
    """
    paragraphs: list[list[str]] = [[]]
    for _, line in _prose_lines(text):
        line = _ESCAPED_BREAK.sub(" ", line).replace("\f", " ").strip()
        if not line:
            paragraphs.append([])
        elif not line.startswith("|"):
            paragraphs[-1].append(line)
    seen: set[str] = set()
    total = repeats = 0
    for lines in paragraphs:
        for sentence in _SENTENCE_END.split(" ".join(lines)):
            if len(sentence.split()) < MIN_SENTENCE_WORDS:
                continue
            key = _NOT_WORD.sub(" ", sentence.casefold()).strip()
            total += 1
            if key in seen:
                repeats += 1
            seen.add(key)
    return repeats, total


def check_source(text: str) -> None:
    """Refuse a source that would print its own markup, or repeat itself to length."""
    problems: list[str] = []
    escaped = escaped_line_breaks(text)
    if escaped:
        first, count = escaped
        problems.append(
            f"The source contains {count} literal \\n escape sequence(s) outside code, "
            f"the first on line {first}. Each prints as a backslash and the letter n "
            "instead of breaking the line, so headings, lists and paragraphs run "
            "together as one block of text. A \\n typed inside a shell echo or printf "
            "argument or a program's string literal is two characters, not a line "
            "break. Write the file with real line breaks; a literal \\n belongs only "
            "in a code span or a fenced block."
        )
    front = front_matter_end(text)
    if front:
        problems.append(
            f"The source starts with a front-matter block ({'line 1' if front == 1 else f'lines 1-{front}'}). "
            "The renderer reads no front matter and prints no header block: the title "
            "is --title, the language is --lang, and whatever the reader should see "
            "belongs in the body. Delete the block."
        )
    repeats, total = repeated_sentences(text)
    if repeats >= MIN_REPEATED_SENTENCES and repeats / total >= MAX_REPEATED_SHARE:
        problems.append(
            f"{repeats} of {total} sentences repeat an earlier sentence word for word "
            f"({round(100 * repeats / total)}%). A document that reaches its length by "
            "repeating itself is padding, not content: write new sentences for the "
            "space, or shorten the document."
        )
    if problems:
        raise PdfError(
            ErrorCode.INVALID_INPUT,
            "Markdown source rejected: " + " ".join(problems),
        )
