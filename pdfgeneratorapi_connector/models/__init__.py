# Load the abstract mixins first so concrete models in bridge addons that
# `_inherit` them can register against the existing AbstractModel entries.
from . import pdfgen_document_mixin  # noqa: I001
from . import pdfgen_send_mixin
from . import (
    pdfgen_api_client,
    pdfgen_async_job,
    pdfgen_model_dataset,
    pdfgen_resolver,
    res_company,
    res_config_settings,
)
