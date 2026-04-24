PDF Generator API Connector — Stock bridge
==========================================

Extends the main ``pdfgeneratorapi_connector`` addon with a dataset + button
for ``stock.picking`` — delivery slips, warehouse receipts, internal
transfers. Install this on top of ``pdfgeneratorapi_connector`` if you use
the Inventory module.

Seeds a pdfgen dataset covering the typical transfer payload (reference,
source document, scheduled + transfer dates, source/destination locations,
company, partner block, responsible user, move lines). Inherits the
``pdfgen.document.mixin`` on ``stock.picking`` so the **Generate custom
PDF** button appears in the header once credentials are configured.

License: LGPL-3.
