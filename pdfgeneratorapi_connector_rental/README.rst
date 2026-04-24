PDF Generator API Connector — Rental bridge
===========================================

Layers rental-specific placeholder paths onto the sale bridge's
``sale.order`` dataset so rental contracts and pickup/return slips have
``rental.start_date``, ``rental.return_date``, ``duration_days``, plus
per-line ``pickup_date`` / ``return_date`` / ``is_rental``.

Pure data addon — no Python, no views. Rental orders in Odoo v18+ are
``sale.order`` records with ``is_rental_order=True``, so the **Generate
custom PDF** button is inherited from the sale bridge.

**Requires Odoo Enterprise** — depends on ``sale_renting``, which ships
with Enterprise only. The addon is not installable on Community.

License: LGPL-3.
