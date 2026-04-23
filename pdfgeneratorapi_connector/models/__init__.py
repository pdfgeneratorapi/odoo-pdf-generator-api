# Load the abstract mixin before any concrete model that `_inherit`s it —
# Odoo's module-loader registers classes during import, so the mixin must
# exist in the registry before account_move references it by name.
from . import pdfgen_document_mixin  # noqa: I001
from . import (
    account_move,
    pdfgen_api_client,
    pdfgen_model_dataset,
    pdfgen_resolver,
    res_config_settings,
)
