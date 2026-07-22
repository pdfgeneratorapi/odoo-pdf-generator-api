{
    "name": "PDF Generator: Sales",
    "version": "18.0.1.0.2",
    "category": "Sales",
    "summary": "Branded PDF quotes and sales orders from Odoo",
    "author": "PDF Generator API",
    "website": "https://pdfgeneratorapi.com",
    "support": "support@pdfgeneratorapi.com",
    "license": "LGPL-3",
    "depends": ["pdfgeneratorapi_connector", "sale"],
    "data": [
        "data/pdfgen_model_dataset_sale_order.xml",
        "views/sale_order_views.xml",
    ],
    "images": ["static/description/pdfgeneratorapi_odoo_cover_560x315.png"],
}
