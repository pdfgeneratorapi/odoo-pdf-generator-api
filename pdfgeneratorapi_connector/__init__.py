# Order matters: models must register their AbstractModels (notably the
# pdfgen.*.mixin family) before the controllers import wizard modules
# whose classes _inherit those mixins. Otherwise Odoo raises
# `Model … inherits from non-existing model 'pdfgen.send.mixin'` at registry
# build time.
from . import models, wizards, controllers  # noqa: I001
