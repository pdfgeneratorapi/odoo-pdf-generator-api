PDF Generator API Connector — Purchase bridge
=============================================

Extends the main ``pdfgeneratorapi_connector`` addon with a dataset + button
for ``purchase.order`` (RFQs and confirmed POs). Install this on top of
``pdfgeneratorapi_connector`` if you use the Purchase module.

Seeds a pdfgen dataset covering the typical purchase order payload (order
number, dates, vendor block, totals, buyer, order lines). Inherits the
``pdfgen.document.mixin`` on ``purchase.order`` so the **Generate custom
PDF** button appears in the header once credentials are configured.

License: LGPL-3.
