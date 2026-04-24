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
- [x] Clean up remaining `pylint-odoo` warnings (`e39d43b`) — superfluous manifest keys dropped, deprecated `description` replaced with per-addon `README.rst`, translation placeholders switched to named `%(status)s / %(body)s`, redundant `string=` kwargs removed. False-positive checks (import-error, duplicate-code, protected-access, too-many-public-methods, etc.) pinned off in `pyproject.toml`. `make lint-pylint` exits clean.
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

- [x] **Template coverage wizard** (`bedd0f4`) — `pdfgen.coverage.wizard` transient model + "Check template coverage" button on the dataset form. Fetches `/templates/{id}/data`, flattens, diffs against the dataset's line paths, reports matched / missing / extra. Now also renders an HTML preview (`POST /documents/generate` with `format=html`) in a sandboxed iframe, using a real record when available and the API's sample data otherwise.
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

### Phase 3.3 — purchase.order bridge (landed)

- [x] New sibling addon `pdfgeneratorapi_connector_purchase`. Depends on `pdfgeneratorapi_connector` + `purchase`.
- [x] Seed dataset for `purchase.order` (~30 lines: scalars including confirmation date / expected date / vendor reference / source document, currency, company, vendor block, totals, buyer, order lines as a list with product/qty/uom/price/discount/subtotal/total/date_planned).
- [x] View inheritance adds **Generate custom PDF** button to the `purchase.order` form header when configured.
- [x] Bridge tests: mixin exposure, action context, seed dataset shape, payload resolution, end-to-end wizard on a purchase.order.
- [x] Makefile/compose/pyproject generalised: `BRIDGES` list is now space-separated and comma-joined via `$(subst $(space),,$(foreach ...))` so adding a bridge only needs one line in each file.

### Phase 3.4 — stock.picking bridge (landed)

- [x] New sibling addon `pdfgeneratorapi_connector_stock`. Depends on `pdfgeneratorapi_connector` + `stock`.
- [x] Seed dataset for `stock.picking` (~25 lines: scalars including reference / source document / scheduled + transfer dates / state / operation type / notes, source + destination locations, company, partner block with full-address expression, responsible user, and move lines as a list with product / description / demand / done / uom — iterating `move_ids` so each product appears once even when split across lots/serials).
- [x] View inheritance adds **Generate custom PDF** button to the `stock.picking` form header when configured.
- [x] Bridge tests: mixin exposure, action context, seed dataset shape, payload resolution, end-to-end wizard on a stock.picking. 5 tests, all green.

### Phase 3.5 — mrp.production bridge (landed)

- [x] New sibling addon `pdfgeneratorapi_connector_mrp`. Depends on `pdfgeneratorapi_connector` + `mrp`.
- [x] Seed dataset for `mrp.production` (~25 lines: scalars including reference / source doc / start+finish+deadline dates / state / priority, product block with name/code/qty-to-produce/qty-produced/uom, BOM reference + type, company block, responsible, and raw-material components as a list iterating `move_raw_ids` with product/code/demand/consumed/uom).
- [x] View inheritance adds **Generate custom PDF** button to `mrp.production` form.
- [x] 5 bridge tests — all green.

### Phase 3.6 — rental bridge (landed, Enterprise-gated)

- [x] New sibling addon `pdfgeneratorapi_connector_rental`. Depends on `pdfgeneratorapi_connector_sale` + `sale_renting`. Installable only on Odoo Enterprise — `sale_renting` ships with Enterprise, not Community.
- [x] Pure data addon (no Python, no views). Extends the sale bridge's `dataset_sale_order` with rental-specific lines: `rental.is_rental`, `rental.start_date`, `rental.return_date`, `rental.duration_days`, plus per-line `is_rental`, `pickup_date`, `return_date`.
- [x] The **Generate custom PDF** button on rental orders is inherited from the sale bridge (rental orders are still `sale.order` records in v18+).
- [x] Not tested locally — Community Odoo can't install `sale_renting`. Manifest + dataset are deliberate: fields match the `sale_renting` schema shipped with Odoo 19 Enterprise. Users on Enterprise can install and verify; a test run against an Enterprise Odoo is a follow-up.

### Initial-version coverage (Sales / Invoicing / Rental / Manufacturing)

With 3.6 landed, every app on the initial-version priority list has a dataset:

- **Sales** → `pdfgeneratorapi_connector_sale` (sale.order) — Phase 3.2.
- **Invoicing** → main `pdfgeneratorapi_connector` module (account.move) — Phase 1.
- **Rental** → `pdfgeneratorapi_connector_rental` extending the sale dataset — Phase 3.6.
- **Manufacturing** → `pdfgeneratorapi_connector_mrp` (mrp.production) — Phase 3.5.

Bonus bridges: `_purchase` (Phase 3.3), `_stock` (Phase 3.4).

### Phase 3.7+ — Further bridges (deferred)

- [ ] `pdfgeneratorapi_connector_project` (`project.task`) — not in initial scope.
- [ ] Custom Studio models — generic entry point exposing the wizard via an action server so any `mail.thread` model can be wired without code.

---

## Phase 4 — Template editor embed

### Phase 4.1 — In-Odoo iframe editor (landed)

- [x] `PdfGenApiClient.get_editor_url(template_id, data=, language=)` → `POST /templates/{id}/editor`.
- [x] `PdfGenApiClient.create_template(name, description=)` → `POST /templates`.
- [x] `pdfgen.template.editor.wizard` TransientModel with live-fetched template Selection, **Open editor** action (writes signed URL to `editor_url` field), **Create new template** action (creates + immediately opens editor on the fresh template).
- [x] Custom OWL field widget `pdfgen_editor_iframe` — assigns the signed URL to `<iframe src>` imperatively. We avoid `widget="html"` with `sandboxedPreview` because that wraps content in a script-blocked sandbox (fine for the HTML preview, fatal for a full JS editor).
- [x] Full-page action (`target=current` — same UX pattern as Settings / Field Datasets). Thin toolbar at the top (template selector + Open editor + new-template name + Create), iframe pane below that swaps in once a URL is set.
- [x] New **Template Editor** menu entry under the PDF Generator API root (admin only).
- [x] 16 Odoo tests (URL storage, shape variants, error paths, create flow, extract helpers) + 4 host unit tests for the new client methods. Combined coverage 97%.

### Phase 4.2 — postMessage handshake (landed)

- [x] `pdfgen_editor_iframe` widget registers a `window.message` listener on mount, cleans up on unmount.
- [x] Every incoming event is dropped unless `event.origin` matches the iframe's current src origin — rejects spoofed save events from other pages.
- [x] Matches the event-type key from either `data.type` or `data.event` (pdfgen's exact shape isn't fully documented, so we accept both common patterns and log the raw payload at `console.debug` level for ongoing discovery).
- [x] Save-type event (anything whose type substring-matches `/save/i`) → success notification "Template saved."
- [x] Close-type event → info notification "Editor closed." + clears `editor_url` so the iframe collapses.
- [x] 6 Hoot tests covering mount, origin validation, save dispatch, close dispatch, unknown-shape rejection, and the `event` key variant.

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

- [x] **i18n** (`54ff98a`): every addon ships a `.pot` + 8 fully-translated `.po` files (es / pt_BR / it / de / fr / cs / sk / et). `make i18n-export` / `i18n-translate` / `i18n-check` targets + `scripts/i18n_translate.py` holds the translation dicts.
- [x] **Multi-company** (`07803f4`): `res.company` extension with the five credential fields. Per-company value wins, global ICP is fallback. `pdfgen.document.mixin.pdfgen_config()` + `build_pdfgen_client()` give every wizard a single read path. Post-migrate copies existing ICP values onto each company for a transparent upgrade. Manifest bumped to `19.0.2.0.0`.
- [x] **Sub-workspace support** (`a44e6bc`): unit test locks in verbatim-forward of the Workspace Identifier into the JWT `sub` claim; README + Settings help text document the sub-workspace format.
- [x] **Rate limiting / retry** (`9e33b94`): 3 retries on 429 / 502 / 503 / 504 + connection errors, honouring `Retry-After` (integer seconds or HTTP-date) with exponential-backoff fallback, per-sleep cap 10s, total cap 30s. Non-retryable 4xx still fail fast.
- [x] **Log redaction** (`6da77fb`): `_redact()` masks values after secret-sounding keys (token / secret / authorization / bearer / jwt / api_key / password) before the WARN log. Applied before the 500-char truncation so a token straddling the boundary still gets caught.
- [x] **Attachment cleanup policy** (`35b6304`): new Settings switch `Keep all versions` (default) vs `Replace previous pdfgen PDFs on the record`. Replace only touches attachments whose `description` starts with `pdfgen:` — manual uploads never get cleaned.

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
