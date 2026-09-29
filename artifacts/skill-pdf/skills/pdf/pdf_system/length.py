"""A page count the user asked for is a requirement of the document.

A request for a ten-page book was delivered as thirty-one pages, and nothing
noticed: the renderer counted the pages it wrote and reported the number, and the
instructions said only that a document beyond its requested length because the
subject needed the room should be delivered. A length the user stated is a limit
as well as a target. The subject never buys extra pages.

The caller passes the count it was asked for, and the renderer, which is where
the pages are counted, enforces it before anything is written. The two
directions are deliberately not symmetrical:

* Over the request by more than a page is refused, and nothing is persisted.
  Cutting is cheap and certain, and the refusal names the measured length so one
  revision converges.
* Short of it by more than a page is delivered and reported, because deepening a
  document takes real time and a run that ends with no file serves nobody. The
  instructions bound that revision to one.
"""
from __future__ import annotations

from .errors import ErrorCode, PdfError

# "Within a page of the request" is what the instructions have always called
# meeting a length: an exact count is not chased.
TOLERANCE_PAGES = 1


def requested_pages(value: object, limit: int) -> int | None:
    """The page count a caller asked for, validated; ``None`` when none was asked."""
    if value is None:
        return None
    text = str(value).strip()
    if not (text.isascii() and text.isdigit() and len(text) <= 6 and 1 <= int(text) <= limit):
        raise PdfError(
            ErrorCode.INVALID_INPUT,
            f"pages must be a whole number of pages between 1 and {limit}, as the user stated it",
        )
    return int(text)


def assess(requested: int, delivered: int) -> str:
    """``over``, ``short`` or ``within`` the request, allowing a page either way."""
    if delivered > requested + TOLERANCE_PAGES:
        return "over"
    if delivered < requested - TOLERANCE_PAGES:
        return "short"
    return "within"


def refuse_if_over(requested: int | None, delivered: int) -> None:
    """Refuse a document longer than the request, naming the measured length."""
    if requested is None or assess(requested, delivered) != "over":
        return
    raise PdfError(
        ErrorCode.INVALID_INPUT,
        f"the document renders to {delivered} pages but {requested} were requested "
        f"(one page either way is allowed), so nothing was written. Shorten the source to "
        f"about {round(100 * requested / delivered)}% of its current length -- cut "
        "sections and detail rather than shrinking the type -- and render again with "
        f"the same --pages {requested}. Dropping or raising --pages does not meet the request.",
    )


def report(requested: int | None, delivered: int) -> dict[str, object] | None:
    """The machine-readable verdict a renderer prints, for a request that named a length."""
    if requested is None:
        return None
    return {"requested": requested, "delivered": delivered, "status": assess(requested, delivered)}
