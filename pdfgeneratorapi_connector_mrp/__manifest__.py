{
    "name": "PDF Generator API Connector — Manufacturing bridge",
    "version": "19.0.1.0.0",
    "category": "Manufacturing",
    "summary": "Generate pdfgeneratorapi.com PDFs from manufacturing orders",
    "description": """
PDF Generator API Connector — Manufacturing bridge
==================================================

Extends the main ``pdfgeneratorapi_connector`` addon so the "Generate custom
PDF" button also appears on manufacturing orders (work orders, production
reports, component pick-lists). Ships a pre-seeded dataset mapping the typical
mrp.production payload.

Install this on top of ``pdfgeneratorapi_connector`` if you use ``mrp``.
""",
    "author": "PDF Generator API",
    "website": "https://pdfgeneratorapi.com",
    "license": "LGPL-3",
    "depends": ["pdfgeneratorapi_connector", "mrp"],
    "data": [
        "data/pdfgen_model_dataset_mrp_production.xml",
        "views/mrp_production_views.xml",
    ],
    "auto_install": False,
    "installable": True,
}
