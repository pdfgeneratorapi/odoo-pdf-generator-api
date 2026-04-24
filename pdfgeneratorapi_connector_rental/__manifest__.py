{
    "name": "PDF Generator API Connector — Rental bridge",
    "version": "19.0.1.0.0",
    "category": "Sales",
    "summary": "Generate pdfgeneratorapi.com PDFs from rental orders (contracts, pickup/return slips)",
    "description": """
PDF Generator API Connector — Rental bridge
===========================================

Extends the Sales bridge dataset with rental-specific placeholder paths
(rental start / return dates, duration, per-line pickup/return). The existing
quotation / sale-order dataset shipped by ``pdfgeneratorapi_connector_sale``
still covers the common fields — this addon just layers rental fields on top
so rental contracts and pickup/return slips have everything they need.

**Requires Enterprise:** depends on ``sale_renting``, which ships with the
Odoo Enterprise license. Community installations don't have the underlying
rental model.

The "Generate custom PDF" button on rental orders is inherited from the
Sales bridge — rental orders are still ``sale.order`` records in v18+.
""",
    "author": "PDF Generator API",
    "website": "https://pdfgeneratorapi.com",
    "license": "LGPL-3",
    "depends": ["pdfgeneratorapi_connector_sale", "sale_renting"],
    "data": [
        "data/pdfgen_model_dataset_rental.xml",
    ],
    "auto_install": False,
    "installable": True,
}
