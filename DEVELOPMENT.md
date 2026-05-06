# Multi-version development

This addon ships separate branches per Odoo major version:

| Branch | Targets |
|---|---|
| `master` | Current dev line (Odoo 19) |
| `19.0` | Mirror of `master` for v19 release pinning |
| `18.0` | Odoo 18 — diverges from `master` for v18 API differences |
| `17.0` | (planned) |

Each branch has its own `__manifest__.py` version prefix (`19.0.x.x.x`,
`18.0.x.x.x`, …) that Odoo validates against the running server's major
version. A bind-mounted source tree can therefore only serve **one**
Odoo major at a time. To run several Odoo versions side-by-side via
Docker — and to keep each branch's pre-commit hook routable to its
matching container — use **git worktrees**, one per branch.

## Layout

```
~/code/ar/
├── pdfgeneratorapi_connector/        ← main checkout, master (= v19 dev)
├── pdfgeneratorapi_connector_v18/    ← worktree on branch 18.0
└── pdfgeneratorapi_connector_v17/    ← worktree on branch 17.0 (when it lands)
```

## One-time setup

```bash
cd ~/code/ar/pdfgeneratorapi_connector

# v18 worktree (sibling directory; absolute path required to keep it
# outside the main repo).
git worktree add ~/code/ar/pdfgeneratorapi_connector_v18 18.0

# v17 worktree, when the 17.0 branch exists:
# git worktree add ~/code/ar/pdfgeneratorapi_connector_v17 17.0

git worktree list   # confirm
```

## docker-compose mounts

`~/code/ar/odoo/docker-compose.yml` mounts each worktree into its
matching Odoo container so each major always sees its own source:

```yaml
odoo:        # v19  ─┐
  volumes:           ├─ → ~/code/ar/pdfgeneratorapi_connector  (master)
                     ┘
odoo18:      # v18  ─┐
  volumes:           ├─ → ~/code/ar/pdfgeneratorapi_connector_v18  (18.0)
                     ┘
odoo17:      # v17  ─┐
  volumes:           ├─ → ~/code/ar/pdfgeneratorapi_connector_v17  (17.0)
                     ┘
```

Switching branches in the **main checkout** (`master` ⇄ a release branch)
no longer affects v18/v17 containers — each worktree is locked to its
branch.

## One Postgres, multiple databases

A single `db` Postgres container hosts one database per Odoo major:

| DB | Used by |
|---|---|
| `odoo` | v19 (`odoo` container) |
| `odoo18` | v18 (`odoo18` container) |
| `odoo17` | v17 (`odoo17` container) — bootstrap when needed |

Each Odoo container is pinned to its DB via `--db-filter=^<dbname>$`
in the compose file's `command:`, so the web UI auto-loads the right
database with no manual selection.

## Bootstrapping a fresh DB for a new version

```bash
# 1. Create the DB
docker exec odoo-db-1 createdb -U odoo odoo18

# 2. Install the addon + all bridges + mrp_account (required so the
#    account.move form spec sees wip_production_count)
cd ~/code/ar/odoo
docker-compose exec odoo18 odoo -d odoo18 -i \
  mrp_account,\
pdfgeneratorapi_connector,\
pdfgeneratorapi_connector_sale,\
pdfgeneratorapi_connector_purchase,\
pdfgeneratorapi_connector_stock,\
pdfgeneratorapi_connector_mrp \
  --stop-after-init
```

## Pre-commit routing

`Makefile` reads `git rev-parse --abbrev-ref HEAD` to pick the matching
Odoo service (`master`/`19.0` → `odoo`, `18.0` → `odoo18`, `17.0` →
`odoo17`). Pre-commit therefore runs the test suite against the right
container automatically when you commit from any worktree.

## Branching policy

- New features land on `master`.
- When a feature needs to ship for an older Odoo major, port via
  cherry-pick or merge into the matching version branch and resolve
  any v18/v17-specific divergences (field renames, view validator
  rules, test fixtures, etc.).
- See `47d6517` for the canonical example of v19 → v18 divergences
  (`account.move.send.wizard.template_id` → `mail_template_id`,
  nameless `<button>`, `product_uom_id` → `product_uom`,
  `stock.move.name` NOT NULL).
