"""Enumerated request-body values for the pdfgeneratorapi.com client."""

from enum import StrEnum


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
