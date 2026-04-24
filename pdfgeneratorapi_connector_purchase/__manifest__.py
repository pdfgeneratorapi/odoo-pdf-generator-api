{
    "name": "PDF Generator API Connector — Purchase bridge",
    "version": "19.0.1.0.0",
    "category": "Purchases",
    "summary": "Generate pdfgeneratorapi.com PDFs from purchase orders",
    "description": """
PDF Generator API Connector — Purchase bridge
=============================================

Extends the main ``pdfgeneratorapi_connector`` addon so the "Generate custom PDF"
button also appears on purchase orders (RFQs and confirmed POs). Ships a
pre-seeded dataset mapping the typical purchase-order payload.

Install this on top of ``pdfgeneratorapi_connector`` if you use ``purchase``.
""",
    "author": "PDF Generator API",
    "website": "https://pdfgeneratorapi.com",
    "license": "LGPL-3",
    "depends": ["pdfgeneratorapi_connector", "purchase"],
    "data": [
        "data/pdfgen_model_dataset_purchase_order.xml",
        "views/purchase_order_views.xml",
    ],
    "auto_install": False,
    "installable": True,
}
