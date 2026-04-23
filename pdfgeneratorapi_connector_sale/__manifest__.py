{
    "name": "PDF Generator API Connector — Sales bridge",
    "version": "19.0.1.0.0",
    "category": "Sales",
    "summary": "Generate pdfgeneratorapi.com PDFs from quotations and sale orders",
    "description": """
PDF Generator API Connector — Sales bridge
==========================================

Extends the main ``pdfgeneratorapi_connector`` addon so the "Generate custom PDF"
button also appears on quotations and sale orders. Ships a pre-seeded dataset
mapping the typical quotation/sale-order payload.

Install this on top of ``pdfgeneratorapi_connector`` if you use ``sale``. For
invoice-only use, the base addon is enough.
""",
    "author": "PDF Generator API",
    "website": "https://pdfgeneratorapi.com",
    "license": "LGPL-3",
    "depends": ["pdfgeneratorapi_connector", "sale"],
    "data": [
        "data/pdfgen_model_dataset_sale_order.xml",
        "views/sale_order_views.xml",
    ],
    "auto_install": False,
    "installable": True,
}
