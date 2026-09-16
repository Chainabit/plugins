"""Secure, capability-oriented PDF artifact system."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import DocumentRequirements, Limits, SecurityPolicy
    from .service import PdfService

__all__ = ("PdfService", "DocumentRequirements", "Limits", "SecurityPolicy")


def __getattr__(name: str) -> object:
    """Preserve the package API without probing optional renderers on import.

    Validators import model and verification submodules, neither of which owns
    renderer discovery.  Eagerly importing ``PdfService`` here made those
    consumers load every optional backend and allowed a broken native renderer
    to write warnings ahead of the validator's single JSON protocol frame.
    """
    if name == "PdfService":
        from .service import PdfService

        value = PdfService
    elif name in {"DocumentRequirements", "Limits", "SecurityPolicy"}:
        from .models import DocumentRequirements, Limits, SecurityPolicy

        value = {
            "DocumentRequirements": DocumentRequirements,
            "Limits": Limits,
            "SecurityPolicy": SecurityPolicy,
        }[name]
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value
