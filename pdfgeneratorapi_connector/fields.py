"""Custom fields for the pdfgen connector."""

from typing import Any

from odoo import fields


class TolerantSelection(fields.Selection):
    """A Selection whose options come from a live external API (pdfgen
    templates), so writes are NOT validated against the current option list.

    A value the API doesn't currently return — because the API is unreachable,
    the list is stale, or it's simply being set in a test — is still stored.
    This matches Odoo 18+, where method-based Selection writes aren't validated;
    Odoo 17 would otherwise raise ``ValueError: Wrong value`` for any such value.
    """

    def convert_to_cache(self, value: Any, record: Any, validate: bool = True) -> Any:
        # Force validate=False: store the value without checking it against the
        # (live, possibly-empty) selection options.
        return super().convert_to_cache(value, record, validate=False)
