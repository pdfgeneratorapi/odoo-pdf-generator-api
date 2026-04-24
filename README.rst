PDF Generator API Connector
===========================

Generate custom PDFs (invoices, quotations, agreements, delivery slips,
manufacturing orders, rental contracts, …) directly from Odoo records via
`pdfgeneratorapi.com <https://pdfgeneratorapi.com>`_. Design templates in
pdfgen's drag-and-drop editor embedded inside Odoo; map any placeholder to
any Odoo field without code.

Installation
------------

1. Drop the ``pdfgeneratorapi_connector`` folder under Odoo's addons path
   (or keep it on a bind mount).
2. Install via *Apps → Update Apps List → PDF Generator API*.
3. Install any bridge addons you need for document types beyond invoices:
   ``pdfgeneratorapi_connector_sale``, ``..._purchase``, ``..._stock``,
   ``..._mrp``, ``..._rental``.

Configuration
-------------

Open **Settings → PDF Generator API** (admin only) and fill in:

- **API Base URL** — regional endpoint from pdfgen, e.g.
  ``https://us1.pdfgeneratorapi.com/api/v4``.
- **API Key** and **API Secret** — copy from the pdfgeneratorapi.com
  account's API page.
- **Workspace Identifier** — your account email, or a sub-workspace
  identifier when you're using sub-workspaces (see below).
- **Editor Web URL** (optional) — override for dev setups where Odoo
  reaches the API via a Docker-internal hostname the browser can't
  resolve. Leave empty for regular pdfgeneratorapi.com users.

Hit **Test Connection** — a green notification confirms the workspace is
reachable.

Sub-workspaces
~~~~~~~~~~~~~~

pdfgeneratorapi.com supports sub-workspaces (scoped template libraries
under a parent account). To use one, enter the sub-workspace identifier
in the *Workspace Identifier* field — pdfgen's convention is
``parent@domain.com:sub-slug`` but check your workspace settings page for
the exact string. The value is passed verbatim into the JWT ``sub`` claim,
so whatever pdfgen expects is what you type here.

Usage
-----

Open any supported document (invoice / sale order / purchase order /
transfer / manufacturing order / rental contract) and click
**Generate custom PDF** in the header. Pick a template from your pdfgen
workspace, click *Generate*, the PDF is attached to the record and posted
to the chatter.

To design templates from inside Odoo: **PDF Generator API → Template
Editor**. Pick an existing template and hit *Open*, or type a name and
hit *Create*. The pdfgen editor loads in an iframe below the selector.

Field mapping
~~~~~~~~~~~~~

**PDF Generator API → Field Datasets** lists one dataset per Odoo model
(account.move, sale.order, …). Each dataset row binds a template
placeholder path to an Odoo field (or an expression composing several
fields, e.g. ``{partner_id.street}, {partner_id.city} {partner_id.zip}``).
The field palette on the right lets you drag any Odoo field onto a row.

License
-------

LGPL-3.

Support
-------

Issues: https://github.com/pdfgeneratorapi/odoo-connector (placeholder).
