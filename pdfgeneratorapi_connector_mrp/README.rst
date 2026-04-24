PDF Generator API Connector — Manufacturing bridge
==================================================

Extends the main ``pdfgeneratorapi_connector`` addon with a dataset + button
for ``mrp.production`` — work orders, production reports, component
pick-lists. Install this on top of ``pdfgeneratorapi_connector`` if you use
the Manufacturing module.

Seeds a pdfgen dataset covering the typical manufacturing order payload
(reference, source document, start/finish/deadline dates, product block,
BOM reference, company, responsible, raw-material components). Inherits
the ``pdfgen.document.mixin`` on ``mrp.production`` so the **Generate
custom PDF** button appears in the header once credentials are configured.

License: LGPL-3.
