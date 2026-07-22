{
    "name": "PDF Generator: MRP",
    "version": "19.0.1.0.1",
    "category": "Manufacturing",
    "summary": "Branded PDF manufacturing orders from Odoo",
    "author": "PDF Generator API",
    "website": "https://pdfgeneratorapi.com",
    "support": "support@pdfgeneratorapi.com",
    "license": "LGPL-3",
    "depends": ["pdfgeneratorapi_connector", "mrp"],
    "data": [
        "data/pdfgen_model_dataset_mrp_production.xml",
        "views/mrp_production_views.xml",
    ],
    "images": ["static/description/pdfgeneratorapi_odoo_cover_560x315.png"],
}
