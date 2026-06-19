================================================
PDF Generator API Connector — Invoicing bridge
================================================

Adds a *Generate custom PDF* button on customer invoices and credit notes,
and seeds a default placeholder dataset for ``account.move``. The send
wizard (``account.move.send.wizard``) also gains a toggle to substitute the
standard invoice report with the latest pdfgen-rendered PDF.

This is the **Invoicing** bridge of the PDF Generator API connector. The
base ``pdfgeneratorapi_connector`` ships only the framework (API client,
mixins, dataset model, wizards, async jobs) — install this bridge to enable
PDF generation against invoices specifically. Other document types are
covered by sibling bridges (Sales, Purchase, Stock, Manufacturing, Rental).
