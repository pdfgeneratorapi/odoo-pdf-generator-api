{
    "name": "PDF Generator: Invoicing",
    "version": "18.0.1.2.1",
    "category": "Accounting",
    "summary": "Generate pdfgeneratorapi.com PDFs from customer invoices and credit notes",
    "author": "PDF Generator API",
    "website": "https://pdfgeneratorapi.com",
    "support": "support@pdfgeneratorapi.com",
    "license": "LGPL-3",
    "depends": ["pdfgeneratorapi_connector", "account"],
    "data": [
        "data/pdfgen_model_dataset_account_move.xml",
        "views/account_move_views.xml",
        "views/account_move_send_views.xml",
    ],
    # Re-home the invoice dataset's ir.model.data rows from the (previously
    # account-coupled) base module to this bridge before the data XML loads.
    # Without this, fresh installs of the new layout work fine but in-place
    # upgrades of existing DBs hit the dataset's unique-per-model constraint.
    "pre_init_hook": "pre_init_hook",
}
