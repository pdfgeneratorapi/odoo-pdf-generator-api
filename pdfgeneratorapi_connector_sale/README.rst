PDF Generator API Connector — Sales bridge
==========================================

Extends the main ``pdfgeneratorapi_connector`` addon with a dataset + button
for ``sale.order`` (quotations, sale orders). Install this on top of
``pdfgeneratorapi_connector`` if you use the Sales module.

Seeds a pdfgen dataset covering the typical quotation / sale order payload
(order number, dates, customer block, totals, salesperson, order lines).
Inherits the ``pdfgen.document.mixin`` on ``sale.order`` so the
**Generate custom PDF** button appears in the header once the main addon's
credentials are configured.

License: LGPL-3.
