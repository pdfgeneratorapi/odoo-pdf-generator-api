from odoo import models


class MrpProduction(models.Model):
    _name = "mrp.production"
    _inherit = ["mrp.production", "pdfgen.document.mixin"]
