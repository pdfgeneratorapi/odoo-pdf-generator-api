{
    "name": "PDF Generator API Connector — Stock bridge",
    "version": "19.0.1.0.0",
    "category": "Inventory",
    "summary": "Generate pdfgeneratorapi.com PDFs from stock transfers (delivery slips, receipts, internal transfers)",
    "description": """
PDF Generator API Connector — Stock bridge
==========================================

Extends the main ``pdfgeneratorapi_connector`` addon so the "Generate custom PDF"
button also appears on stock transfers — typically used for delivery slips,
warehouse receipts, and internal transfer sheets. Ships a pre-seeded dataset
mapping the typical stock.picking payload.

Install this on top of ``pdfgeneratorapi_connector`` if you use ``stock``.
""",
    "author": "PDF Generator API",
    "website": "https://pdfgeneratorapi.com",
    "license": "LGPL-3",
    "depends": ["pdfgeneratorapi_connector", "stock"],
    "data": [
        "data/pdfgen_model_dataset_stock_picking.xml",
        "views/stock_picking_views.xml",
    ],
    "auto_install": False,
    "installable": True,
}
