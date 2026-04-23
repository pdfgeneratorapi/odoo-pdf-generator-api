{
    "name": "PDF Generator API Connector",
    "version": "19.0.1.0.0",
    "category": "Accounting",
    "summary": "Generate invoices, quotes and other documents via pdfgeneratorapi.com",
    "description": """
PDF Generator API Connector
===========================

Integrates pdfgeneratorapi.com with Odoo so users can design document templates
in a drag-and-drop editor and generate branded PDFs (invoices, quotations,
agreements, manufacturing orders) directly from Odoo records.

Requires a pdfgeneratorapi.com account. Data from the Odoo record (invoice
lines, partner details, totals, etc.) is transmitted to pdfgeneratorapi.com
over HTTPS so the template engine can merge it into the chosen template. See
README and the Settings page for the full list of transmitted fields.

Hosting: supported on Odoo.sh, on-premise Enterprise, and Community Edition.
Not supported on Odoo Online (SaaS), which does not allow third-party apps.
""",
    "author": "PDF Generator API",
    "website": "https://pdfgeneratorapi.com",
    "license": "LGPL-3",
    "depends": ["base", "mail", "account"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "views/res_config_settings_views.xml",
        "views/pdfgen_coverage_wizard_views.xml",
        "views/pdfgen_model_dataset_views.xml",
        "views/generate_pdf_wizard_views.xml",
        "views/account_move_views.xml",
        "views/menu.xml",
        "data/pdfgen_model_dataset_account_move.xml",
    ],
    "external_dependencies": {},
    "assets": {
        "web.assets_backend": [
            "pdfgeneratorapi_connector/static/src/mapping_editor/field_palette.js",
            "pdfgeneratorapi_connector/static/src/mapping_editor/field_palette.xml",
            "pdfgeneratorapi_connector/static/src/mapping_editor/droppable_field_selector.js",
            "pdfgeneratorapi_connector/static/src/mapping_editor/mapping_editor.scss",
        ],
    },
    "images": ["static/description/icon.png"],
    "application": True,
    "installable": True,
}
