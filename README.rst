===========================
PDF Generator API Connector
===========================

Generate custom-designed PDFs from Odoo records using `pdfgeneratorapi.com
<https://pdfgeneratorapi.com>`_.

What it does
============

- Adds a **Generate custom PDF** button on customer invoices.
- Pulls the list of templates from your PDF Generator API workspace and lets
  you pick one.
- Serializes the invoice (header, lines, partner, totals, currency) and sends
  it to pdfgeneratorapi.com for rendering.
- Attaches the returned PDF to the invoice as an ``ir.attachment`` and
  downloads it in the browser.

Hosting compatibility
=====================

============================  =========  =========
Hosting                        Install?   Outbound?
============================  =========  =========
Odoo.sh                        Yes        Yes
Odoo on-premise Enterprise     Yes        Yes
Community Edition              Yes        Yes
Odoo Online (SaaS)             No         N/A
============================  =========  =========

Odoo Online does not allow third-party apps; this module cannot run there.

Configuration
=============

1. Sign up at https://pdfgeneratorapi.com and note your API key, API secret,
   and workspace identifier (usually your account email).
2. In Odoo: **Settings > PDF Generator API**.
3. Paste the key, secret, and workspace identifier. Click **Test Connection**.

Privacy
=======

When you generate a PDF, the following record data is transmitted to
pdfgeneratorapi.com over HTTPS: invoice number, dates, state, currency,
company and customer details (name, address, VAT), line items (description,
quantity, price, taxes), totals, payment reference, and notes. The request
is signed with a short-lived JWT (HS256) using your API secret. No data is
transmitted outside the Generate action.

License
=======

LGPL-3.
