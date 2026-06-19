"""Enumerated request-body values for the pdfgeneratorapi.com client."""

import sys

if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # Odoo 17 runs on Python 3.10, where StrEnum is not in the stdlib yet.
    from enum import Enum

    class StrEnum(str, Enum):
        """Backport of 3.11's StrEnum: members are real str values, and
        str()/format() yield the value rather than ``ClassName.MEMBER``."""

        __str__ = str.__str__
        __format__ = str.__format__  # type: ignore[assignment]


class Format(StrEnum):
    """Document format the API renders — the request body's `format` field.

    Lists the formats this connector uses; the API supports more (add as needed).
    """

    PDF = "pdf"
    HTML = "html"


class Output(StrEnum):
    """How the rendered document is returned — the request body's `output` field."""

    BASE64 = "base64"
    URL = "url"
