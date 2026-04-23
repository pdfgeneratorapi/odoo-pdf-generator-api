from odoo import models


class SaleOrder(models.Model):
    _name = "sale.order"
    _inherit = ["sale.order", "pdfgen.document.mixin"]
