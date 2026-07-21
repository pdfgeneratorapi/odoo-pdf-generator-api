---
name: backport-release
description: "Backport a merged change from master (Odoo 19) to the 18.0 and 17.0 lines, verify it on each, and cut the version bumps. Use when asked to backport, port to 18/17, sync the version branches, or bump versions across lines. Also covers the commit/PR conventions for this repo."
user-invocable: true
---

# Backporting and releasing the pdfgen connector

Four lines: `master` (dev, Odoo 19), `19.0` (mirror of master), `18.0`, `17.0`.
A change lands on master via PR, then gets cherry-picked to 18.0 and 17.0.

---

## Where the work happens

Each version branch has its own git worktree, permanently mounted into its
own Odoo container. **Never** switch branches in a worktree — that swaps
the source under a running container of the wrong major.

| Line | Worktree | Container | DB (CLI/tests) | DB (web UI) | Web |
|---|---|---|---|---|---|
| master / 19.0 | `~/code/ar/pdfgeneratorapi_connector` | `odoo` | `odoo` | `odoo` | :8019 |
| 18.0 | `~/code/ar/pdfgeneratorapi_connector_v18` | `odoo18` | `odoo18_v18` | `odoo18` | :8018 |
| 17.0 | `~/code/ar/pdfgeneratorapi_connector_v17` | `odoo17` | `odoo17_v17` | `odoo17` (absent) | :8017 |

Compose lives in `~/code/ar/odoo/docker-compose.yml`. Start a line's
container with `docker compose up -d odoo18` before doing anything — the
pre-commit hook runs the suite in it and fails hard if it is down.

The Makefile routes `ODOO_SERVICE` off the **branch name** and `ODOO_DB`
off the resolved service, so on `18.0`/`17.0` you pass nothing. On a
`feature/*` branch in a version worktree you must pass
`ODOO_SERVICE=odoo18` (the DB follows).

---

## The backport recipe

1. **List what to bring over.** `git log --oneline <line-head>..master --no-merges`.
   Squash-merged PRs appear as one commit; cherry-pick that, not the
   original branch commits (their SHAs are not ancestors of master).

2. **Cherry-pick in order**, oldest first. Vendor/asset commits usually
   apply clean; code commits conflict on:
   - **`__manifest__.py`** — always. Take `--theirs`, then rewrite the
     serie: `"version": "19.0.` → `"18.0.` / `"17.0.`. The serie is
     enforced at module load, so a missed one is an install error.
   - **`i18n/*.po` and `*.pot`** — always, and the content is wrong
     either way: `.pot` files are exported per line from that line's DB.
     Take `--theirs` to get past the conflict, then re-export (step 5).
   - **model files the line has already diverged in** — read the hunk.
     The account Send wizard is the usual one (17 has an extra
     `_pdfgen_move()` guard, 18/17 use `mail_template_id`). Keep both
     sides' intent; do not blanket-take either.

3. **Apply the compat deltas** before `--continue`. Check the new code
   against the target major *in its own container*, e.g.
   `docker compose exec -T odoo17 sh -c 'grep -n "<api>" /usr/lib/python3/dist-packages/odoo/addons/<addon>/...'`.
   See the checklist below.

4. **`git cherry-pick --continue`** — the pre-commit hook runs lint +
   the full suite + the 95% coverage gate in that line's container. A
   green continue means the port actually works on that major.

5. **Re-export translations** on the line:
   `make i18n-export && make i18n-translate && make i18n-check`.
   Then confirm nothing regressed to English: the untranslated count per
   `.po` should match what it was before (15 at the time of writing).
   `scripts/i18n_translate.py` is a single cross-line superset dict —
   add missing msgids there, never per line.

6. **Verify in the browser.** Python tests do not catch view-arch or
   widget breakage. See "Browser verification" below.

7. **Bump and push.** `git push origin 18.0`.

---

## Compat checklist (19 → 18 → 17)

Confirmed deltas. Grep the target container before assuming an API exists.

**18 and 17**
- `account.move.send.wizard.template_id` (19) is `mail_template_id`; no
  `pdf_report_id`. Fix `@api.depends` on the widget compute.
- Odoo 17's Send & Print is one multi-move wizard (`account.move.send`,
  `move_ids`), not per-move.
- Every `<button>` needs a `name` (18 view validator; 19 relaxed it).
- A non-stored compute **with no `@api.depends` renders blank** on 17/18.
  Use `default=lambda self: ...` instead. A bare `@api.depends()` does
  not fix it. Assert via `default_get([...])`, not attribute access.
- The composer's web client **round-trips a field edit through
  `onchange`**, where 19 goes straight to `web_save` with the x2many
  already in the payload. Code that must react to a field edit needs
  *both* the field in a stored compute's `@api.depends` **and** a
  `create`/`write` hook. (This is why `_compute_attachment_ids` lists
  the pdfgen fields.)
- Dev seeder (`~/code/ar/odoo/addons/pdfgen_demo_data`): `stock.move.name`
  is NOT NULL; outgoing picking types can have blank default locations.

**17 only**
- Python 3.10 — no `enum.StrEnum`, no `typing.Self`.
- Views use `<tree>`, not `<list>`; `view_mode` `list` → `tree`.
- Dynamic `Selection` writes are validated against live options — use
  `TolerantSelection` from `..fields` for any API-backed selection.
- `self.env._(...)` is 18+; use the module-level `_`.
- Every `static/src` JS file needs `/** @odoo-module **/`.
- `product.product.is_storable` is 18+; 17 uses `type='product'`.
- The mail composer attaches with `widget="many2many_binary"`;
  `mail_composer_attachment_list` is 18+. Any xpath onto that field must
  match per line.
- `SelectionField` renders a native `<select>`; 19 uses `SelectMenu`.
- In tests, `.new({"m2m": [(6,0,[id])]})` does not populate an x2many —
  use `.create(...)`.
- Odoo ≤18 has no `odoo i18n` CLI; the Makefile uses the legacy
  `--i18n-export=` form (already handled per line).
- No `/odoo/<model>/<id>` URLs — the web client is `/web#action=<id>&…`.

---

## Browser verification

Frontend breakage (view inherits, widgets, JS) shows up **only** here.

- 19: `http://localhost:8019/odoo/sales/<id>`
- 18: `http://localhost:8018/odoo/sales/<id>`
- 17: `http://localhost:8017/web?#action=<action_id>&model=sale.order&view_type=list`
  (get the action id from `ir_model_data`)

The UI serves the DB matching the container's `--db-filter`, which is
**not** the CLI test DB. For 17 there is no `odoo17` DB, so either seed
`odoo17_v17` and temporarily repoint the filter in the compose file
(`^odoo17_v17$`, `docker compose up -d odoo17`, then revert), or drive
the check from `docker compose exec -T odoo17 odoo shell -d odoo17_v17`
and `env.cr.rollback()` at the end.

Records and credentials for a fresh line DB:

```bash
make demo-seed COUNT=3                     # in the line's worktree
# copy API creds from the 19 DB:
docker compose exec -T db psql -U odoo -d odoo -tAc \
  "select value from ir_config_parameter where key='pdfgen.api_key'"
# → insert into the target DB's ir_config_parameter (… on conflict do update)
```

---

## Versions

Keep the x.y.z aligned across lines; only the serie differs:
`19.0.7.3.1` / `18.0.7.3.1` / `17.0.7.3.1`. Bump the module whose code
changed (usually the base addon), in the same commit as the change on
master and in the backport commit on each line. Annotated tags track the
manifest version.

---

## Commits and PRs

- Work on master goes on a branch (`feature/…`, `fix/…`, `chore/…`) and
  lands via PR. Chores/refactors get their own branch off the feature
  branch rather than being mixed in.
- Backports are committed **directly on** `18.0` / `17.0`.
- Commit messages: imperative subject describing the user-visible
  outcome, then prose explaining *why* and what was ruled out. Explain
  surprises (a framework behaviour you had to work around) — that is the
  part nobody can reconstruct later. No bullet-point dumps of the diff.
- Let the pre-commit hook run (lint, tests, ≥95% coverage). Do not
  `--no-verify` to get past a red suite; the one legitimate use is a
  pure-metadata commit when the container is intentionally down.
- After a squash-merge, the branch's commits are *not* ancestors of
  master. A stacked PR will then look "conflicted" — check with
  `git diff master <branch>` before resolving anything; if the diff is
  mostly deletions, the content is already in and the PR should be
  closed, not merged.
