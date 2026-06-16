{
    "name": "PDF Generator API Connector — Stock bridge",
    "version": "18.0.1.0.0",
    "category": "Inventory",
    "summary": "Generate pdfgeneratorapi.com PDFs from stock transfers (delivery slips, receipts)",
    "author": "PDF Generator API",
    "website": "https://pdfgeneratorapi.com",
    "license": "LGPL-3",
    "depends": ["pdfgeneratorapi_connector", "stock"],
    "data": [
        "data/pdfgen_model_dataset_stock_picking.xml",
        "views/stock_picking_views.xml",
    ],
}
