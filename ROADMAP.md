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
- [x] Hardcoded invoice serializer (partner, lines, totals, taxes)
- [x] `POST /documents/generate` → `ir.attachment` on the invoice
- [x] Docker compose: v19 + v18 services, shared network with pdfgeneratorapi backend
- [x] Dev tooling: `uv`, `ruff`, `pylint-odoo`, `pre-commit` hook with 95% coverage gate
- [x] 25 tests green (11 host unit + 14 Odoo integration), 98% coverage

### Open Phase 1 follow-ups

- [ ] v18 parity: full manual smoke test on the `odoo18` service (install, Test Connection, template list, generate, attach).
- [ ] Clean up remaining `pylint-odoo` warnings: `prefer-env-translation`, `translation-positional-used`, `attribute-string-redundant`. Run `make lint-pylint` to see the list.
- [ ] Surface `list_templates` errors to the UI instead of silently returning `[]` (currently users see an empty dropdown with no explanation).
- [ ] CI: GitHub Actions workflow running `make lint` + `make coverage` on PRs. Needs a minimal odoo-in-docker harness in the runner.
- [ ] Polish `README.rst` with install/config/usage screenshots — required for App Store submission.

---

## Phase 2 — Field mapper UI

Goal: let users target any placeholder in a pdfgen template to any Odoo field, without code changes.

- [ ] New `pdfgen.template_mapping` model — keyed by `(remote_template_id, odoo_model)`, stores mapping rules as JSON. **Not** a cached copy of remote templates.
- [ ] OWL component: side-by-side view — template placeholders (left, from `GET /templates/{id}`) vs. Odoo field picker (right, introspection via `ir.model.fields`).
- [ ] Drag-to-map / click-to-bind interaction.
- [ ] Expression support for computed fields (`partner_id.commercial_partner_id.vat`, arithmetic on line items).
- [ ] Fall back to the hardcoded invoice serializer when no mapping exists (backward compatibility).
- [ ] Extend the wizard to look up the mapping for the selected template and build the payload from the mapping rules.
- [ ] Unit + integration tests for the mapping resolver (edge cases: circular refs, missing fields, typed conversions).

---

## Phase 3 — Additional document types

Targets beyond invoices. For each: inherit the originating model, add the "Generate custom PDF" action + wizard flow.

- [ ] `sale.order` (quotations)
- [ ] `purchase.order`
- [ ] `stock.picking` (delivery slips)
- [ ] `mrp.production` (manufacturing orders)
- [ ] `project.task` (work orders / agreements)
- [ ] Rental flows (`sale.order` with `is_rental=True`, or `rental.order` depending on version)
- [ ] Custom Studio models (generic entry point — expose the wizard on any `mail.thread` model via an action server)

Each one is independent work; Phase 2 mapper makes them mostly configuration rather than code.

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

## Future improvements (deferred from Phase 2.1)

These were scoped out of the first mapping slice to keep it shippable; revisit once v2.1 ships and real users have tried it.

- [ ] **Expression support** — map a placeholder to a computed callable (`total_in_words`, `format_date('DD MMM YYYY', invoice_date)`, arithmetic on lines). Needs either a sandboxed eval (`safe_eval`) or a named-helper registry (`pdfgen.helpers`) plus a UI for picking a helper.
- [ ] **Multi-level nested lists** — `{{#pages}}{{#lines}}...{{/lines}}{{/pages}}` style templates. v2.1 supports one level of list iteration; nested lists need a recursive resolver and a tree-aware UI.
- [ ] **Conditional placeholders** — emit a placeholder only when a field is truthy (`{{#has_discount}}...{{/has_discount}}`). Today the resolver always emits the key.
- [ ] **Default values** — fall back to a literal when an Odoo field is empty (e.g., blank `vat` → `"N/A"`).
- [ ] **Preview button** — show the resolved JSON payload for a sample record before calling `/documents/generate`. Makes debugging mapping mistakes much faster than reading the rendered PDF.
- [ ] **Reuse mapping across templates** — "Copy from…" action to seed a new mapping from an existing one.
- [ ] **Mapping versioning** — keep a history so bumping a template doesn't silently break in-flight flows.
- [ ] **Bulk-field picker** — "auto-map by name" button that matches placeholder paths to Odoo fields by fuzzy name (e.g. `customer_name` → `partner_id.name`).

## Reference

- Strategy doc: `~/Downloads/odoo_pdfgeneratorapi_strategy 2.pdf` (April 2026, v1.0).
- v1 plan: `~/.claude/plans/so-how-do-we-wondrous-graham.md`.
- API docs: https://docs.pdfgeneratorapi.com/v4.
