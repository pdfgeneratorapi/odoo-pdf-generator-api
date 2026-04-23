# Roadmap

Progress tracker for the `pdfgeneratorapi_connector` Odoo addon. Phases mirror
the April 2026 strategy doc; checked items are landed on `main`.

---

## Phase 1 — Walking skeleton + single-doc generation (v1)

- [x] Module scaffold, manifest, menu, ACLs
- [x] `res.config.settings` page with credentials + Show/Hide secret toggle
- [x] Hand-rolled `PdfGenApiClient` (stdlib + `requests`, zero pip deps)
- [x] JWT HS256 minting (fresh token per request)
- [x] `Test Connection` button hitting `GET /workspaces/{id}`
- [x] `Generate custom PDF` button on `account.move`
- [x] Wizard with live `GET /templates` (first 100)
- [x] `POST /documents/generate` → `ir.attachment` on the invoice, posted to the chatter
- [x] Docker compose: v19 + v18 services, shared network with pdfgeneratorapi backend
- [x] Dev tooling: `uv`, `ruff`, `pylint-odoo`, `pre-commit` hook with 95% coverage gate

### Open Phase 1 follow-ups

- [ ] v18 parity: full manual smoke test on the `odoo18` service (install, Test Connection, template list, generate, attach).
- [ ] Clean up remaining `pylint-odoo` warnings: `prefer-env-translation`, `translation-positional-used`, `attribute-string-redundant`. Run `make lint-pylint` to see the list.
- [ ] Surface `list_templates` errors to the UI instead of silently returning `[]` (currently users see an empty dropdown with no explanation).
- [ ] CI: GitHub Actions workflow running `make lint` + `make coverage` on PRs. Needs a minimal odoo-in-docker harness in the runner.
- [ ] Polish `README.rst` with install/config/usage screenshots — required for App Store submission.

---

## Phase 2 — Field mapper UI

Goal: let users target any placeholder in a pdfgen template to any Odoo field, without code changes.

### Phase 2.1 — Per-template mapping model (superseded by 2.3)

First attempt keyed the mapping per remote template. Usable, but required re-binding the same fields for every new template — abandoned in favour of Phase 2.3's shared dataset-per-model.

### Phase 2.2 — OWL field palette + drag-to-bind (landed)

- [x] Custom `view_widgets` OWL component — browsable, filterable field tree for the selected Odoo model with breadcrumb drill-down into relations.
- [x] Uses the stock `field` service (`loadFields` → `fields_get`) so any field Odoo recognises is pickable.
- [x] Pointer-event-based drag (bypasses native HTML5 drag which OWL / list editor interferes with) with a floating ghost and a row-level highlight under the cursor.
- [x] Drop writes the bound path directly on the matching line record via `record.update()` — no ORM round-trip, no need to enter edit mode first.
- [x] Tests + coverage gate still green at ≥95%.

### Phase 2.3 — Shared dataset per Odoo model (landed)

- [x] `pdfgen.model.dataset` model (plus `pdfgen.model.dataset.line`), unique per Odoo model — one invoice dataset serves every pdfgen template.
- [x] Seed data `data/pdfgen_model_dataset_account_move.xml` with ~30 pre-filled placeholder→field mappings matching the payload the old hardcoded serializer produced.
- [x] **Expression column** on each line — template-string syntax `{dotted.path}` to compose multiple Odoo fields (e.g. `customer.full_address = {partner_id.street}, {partner_id.city} {partner_id.zip}`). Beats `odoo_field_path` when set.
- [x] Palette drag-drop is expression-aware: dropping onto a row with an expression appends ` {path}`; dropping onto a row with a bare path promotes it to an expression before appending.
- [x] Menu renamed **Template Mappings → Field Datasets**. Old `pdfgen.template.mapping` model + views + tests deleted; "Load placeholders" flow removed (no longer meaningful — schema is now model-defined, not template-defined).
- [x] Wizard looks up the dataset by `('model', '=', 'account.move')`, builds the payload, sends it unchanged to whatever template the user picks.
- [x] Coverage at 99% across 50 host unit + 23 Odoo integration tests.

### Phase 2.3 gap closeout (landed)

- [x] **Template coverage wizard** (`bedd0f4`) — `pdfgen.coverage.wizard` transient model + "Check template coverage" button on the dataset form. Fetches `/templates/{id}/data`, flattens, diffs against the dataset's line paths, reports matched / missing / extra.
- [x] **Keyboard navigation** (`6f6189b`) — Arrows navigate the palette list, Enter drills into relations, Esc/Backspace pop the breadcrumb, `/` focuses the filter input. Focused-row highlight shows only while the palette has focus.
- [x] **Hoot tests** (`2a4afb6`) — `web.assets_unit_tests` bundle + `static/tests/field_palette.test.js` covering initial load, filter, drill-in, breadcrumb rewind, keyboard shortcuts. Pointer-drag binding is left to manual verification (fragile under hoot's window-level simulation; Python-side wizard tests already cover the record.update contract).

---

## Phase 3 — Additional document types

Per-doc-type bridge modules, each depending on the main `pdfgeneratorapi_connector` + the respective Odoo module. Users install only the bridges they need; the main module stays lean (base/mail/account).

### Phase 3.1 — Generic wizard + mixin (landed, `e31cce6`)

- [x] `pdfgen.generate.wizard` refactored from a hardcoded `account.move` Many2one to a generic `(res_model, res_id)` pair.
- [x] New `pdfgen.document.mixin` abstract model exposing `pdfgen_configured` + `action_open_pdfgen_wizard`. Target models just `_inherit` the mixin; bridges add a view to surface the button.
- [x] `account.move` migrated to use the mixin (no duplication left in the main module).
- [x] Wizard attachment posts to the chatter only when the source model supports `message_post`.

### Phase 3.2 — sale.order bridge (landed)

- [x] New sibling addon `pdfgeneratorapi_connector_sale`. Depends on `pdfgeneratorapi_connector` + `sale`.
- [x] Seed dataset for `sale.order` (~25 lines: scalars, currency, company, customer, totals, salesperson, order lines as a list section with product/qty/uom/price/discount/subtotal/total).
- [x] View inheritance adds **Generate custom PDF** button to the `sale.order` form header when configured.
- [x] Bridge tests: mixin exposure, seed dataset shape, payload resolution, end-to-end wizard on a sale.order.
- [x] Docker compose + Makefile updated to mount and drive both addons; pre-commit's `make coverage` covers the bridge (97% combined).

### Phase 3.3+ — Remaining bridges (pending)

Each follows the same pattern as `pdfgeneratorapi_connector_sale`: new addon dir, manifest depending on the Odoo module, `_inherit` the mixin, seed dataset, view inheritance for the button, tests.

- [ ] `pdfgeneratorapi_connector_purchase` (`purchase.order`)
- [ ] `pdfgeneratorapi_connector_stock` (`stock.picking` — delivery slips)
- [ ] `pdfgeneratorapi_connector_mrp` (`mrp.production`)
- [ ] `pdfgeneratorapi_connector_project` (`project.task`)
- [ ] Rental flows (`sale.order` with `is_rental=True` in v18+, or `rental.order` in earlier/Enterprise variants)
- [ ] Custom Studio models — generic entry point exposing the wizard via an action server so any `mail.thread` model can be wired without code.

---

## Phase 4 — Template editor embed

- [ ] `POST /templates/{id}/editor` to get a signed editor URL.
- [ ] Iframe embed inside Odoo (wizard or full-page client action).
- [ ] Handle the `postMessage` handshake if the editor sends events back.
- [ ] Create-template flow (`POST /templates`) from the Odoo side so users never have to visit pdfgeneratorapi.com.

---

## Phase 5 — Batch generation

- [ ] Batch flow from list view: select N invoices → pick template → single PDF with all of them.
- [ ] Use `POST /documents/generate/batch` (sync) for small batches (≤10).
- [ ] Use `POST /documents/generate/batch/async` + webhook callback for larger batches.
- [ ] Webhook receiver (`/pdfgen/webhook`) with signature verification.
- [ ] `pdfgen.batch` model to track async job state (pending / completed / failed), visible as a list view.
- [ ] Per-user email notification when batch completes.

---

## Phase 6 — Async single-doc (if needed)

Only wire this up if real-world templates regularly exceed ~30s:

- [ ] Switch `action_generate` to `POST /documents/generate/async` when template has an `async: true` flag or when config threshold is exceeded.
- [ ] Poll or webhook for completion.
- [ ] Progress indicator on the wizard.

---

## Phase 7 — Distribution & App Store

- [ ] `static/description/index.html` — App Store listing page (features, screenshots).
- [ ] Proper icon (currently placeholder).
- [ ] Screenshots: settings page, wizard, generated PDF attached to invoice.
- [ ] Privacy policy section for App Store review (what data leaves Odoo, over TLS, to which region).
- [ ] Run `pylint-odoo` at the P-level strict setting — App Store reviewers do.
- [ ] Submit to Odoo App Store for v19. Tag `19.0.1.0.0`.
- [ ] Backport to `18.0` branch, submit separately.
- [ ] Backport to `17.0` branch (deferred per initial plan).

---

## Cross-cutting concerns

- [ ] i18n: extract translations via `make i18n`, contribute at minimum `es`, `pt_BR`, `it`, `de`, `fr` `.po` files.
- [ ] Multi-company: currently uses a single set of `ir.config_parameter` values; if users want per-company workspaces, add a `res.company` extension.
- [ ] Sub-workspace support: verify that setting the `sub` claim to a sub-workspace identifier actually routes templates correctly.
- [ ] Rate limiting / retry with backoff on 429 responses.
- [ ] Log redaction: ensure we never log the API secret or the raw JWT (currently the secret is in `ir.config_parameter`, visible only to `base.group_system`, but audit the log output).
- [ ] Attachment cleanup policy: should we delete old generated PDFs on re-generation, keep all versions, or let the user decide via a setting?

---

## Future improvements (deferred from Phase 2)

Scoped out of Phase 2.1/2.2/2.3 to keep each slice shippable; revisit once the addon has seen real use.

- [x] ~~**Expression support**~~ — landed in 2.3 as template-string composition (`{dotted.path}` tokens). Format specifiers / conditionals still deferred.
- [ ] **Format specifiers in expressions** — `{amount|currency}`, `{invoice_date|date:DD MMM YYYY}`. Needs a small helper registry.
- [ ] **Conditional / fallback operators in expressions** — `{vat or "N/A"}`, `{#has_discount}…{/has_discount}`.
- [ ] **Multi-level nested lists** — `{{#pages}}{{#lines}}...{{/lines}}{{/pages}}` style templates. One level works; recursive list iteration needs a tree-aware dataset UI.
- [ ] **Per-template overrides** — if a specific template needs a divergent payload shape, no built-in way today. Add a template-scoped override layer on top of the dataset if real use demands.
- [ ] **Preview button** — show the resolved JSON payload for a sample record before calling `/documents/generate`. Makes debugging mapping mistakes much faster than reading the rendered PDF.
- [ ] **"Check template coverage"** — fetch the selected template's `/data` endpoint, diff against the dataset, warn about placeholders the dataset doesn't provide.
- [ ] **Bulk-field picker** — "auto-map by name" button that matches placeholder paths to Odoo fields by fuzzy name (e.g. `customer_name` → `partner_id.name`).
- [ ] **Dataset versioning** — keep a history so bumping the dataset doesn't silently break in-flight flows.
- [ ] **Warn when a selected template uses placeholders the dataset doesn't provide** — pre-flight check on `action_generate` that runs the coverage diff and surfaces missing keys.

## Reference

- Strategy doc: `~/Downloads/odoo_pdfgeneratorapi_strategy 2.pdf` (April 2026, v1.0).
- v1 plan: `~/.claude/plans/so-how-do-we-wondrous-graham.md`.
- API docs: https://docs.pdfgeneratorapi.com/v4.
