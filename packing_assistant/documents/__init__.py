"""Versioned document operations. Models propose patches; this package applies them."""

from .service import handle

__all__ = ["handle"]
