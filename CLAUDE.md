# Reconcile

Single-user check register PWA. FastAPI + Jinja2 + htmx + Alpine.js + SQLite (stdlib `sqlite3`, no ORM). Design spec: `docs/superpowers/specs/2026-10-18-reconcile-design.md`.

## Commands

- Tests: `uv run pytest`
- Dev server: `uv run uvicorn app.main:app --reload --reload-dir app --reload-dir static --reload-dir templates --reload-include '*.css' --reload-include '*.js' --reload-include '*.html'`
- Release (prod is amd64, dev machine is arm64): `scripts/deploy-reconcile-from-arm64.sh vX.Y.Z`. It tags and pushes the tag, builds and pushes `docker.io/xadamz23/reconcile:<tag>` (no `:latest`), then SSHes to the server and runs `update-app.sh <tag>`. That script (a copy is in `scripts/`) rewrites the server's `docker-compose.yaml` with the tag and restarts. It doesn't run tests. The repo's `compose.yaml` is a generic `.env`-driven version the server doesn't use.
- Regenerate icons: `uv run --no-project --with resvg-py --with pillow python scripts/make_icons.py`

## Layout

- `app/repo.py`: **all SQL**. Routes never write SQL themselves. Text is trimmed in `TxnInput.__post_init__` / `clean_text`, which is the one place whitespace stripping happens.
- `app/money.py`: amounts are **signed integer cents** (negative = payment). Never use floats.
- `app/db.py`: migrations are an append-only list keyed by `PRAGMA user_version`. Add a new entry and never edit an old one.
- `app/assets.py`: hashes every file in `static/` at startup. Templates must reference assets via `static_url('path')`, which yields `?v=<hash>` URLs that are served `immutable`.
- `app/routes/`: one router per area. `deps.render()` injects `today`.
- `templates/_*.html` are htmx partials. `templates/sw.js` is the service worker, rendered by `routes/pwa.py`.

## Gotchas

- Asset hashes are computed at startup. Without the `--reload-include` flags (which need `watchfiles`, a dev dep), CSS/JS edits keep their old hash and the browser serves the old immutable copy.
- htmx `attributesToSettle` is set to `[]` in `base.html`. The default settle step copies `class`/`style` between same-id elements and breaks Alpine's `x-show`.
- After a save, routes return `HX-Reswap: none` plus `HX-Trigger: ledger-changed, panel-close`. `#ledger` refetches itself on `ledger-changed`. htmx still fires `afterSwap` on these responses, so `app.js` opens the panel only on GET.
- Running balance is a window function over the **whole** ledger, applied before filters, so filtered rows still show true balances.
- Statuses are `unverified` → `verified` → `reconciled`. Toggling never touches `reconciled`. `update_txn` keeps `reconciled` as is. The opening balance (`is_opening=1`) is reconciled and can't be deleted.
- Cleared balance for reconciling = all reconciled + verified items dated on or before the statement's closing date.
- CSRF: middleware in `app/main.py` rejects any non-GET request that a browser marks as cross-site (`Sec-Fetch-Site`, falling back to `Origin` vs `Host`). Auth is only Cloudflare Access, so this is the one thing stopping other sites from posting forms. Requests with neither header (curl, TestClient) are allowed.
- `scripts/import_register_csv.py` is stdlib-only (it runs on the prod host, not in the venv) and writes SQL directly, against `SCHEMA_VERSION`. Bump that constant and re-check its INSERT when adding a migration.
- The transaction amount input (`data-cents`) uses cash-register entry: digits fill from the right and there's no decimal key. The sign comes from the Payment/Deposit toggle. It's handled in `app.js`. The opening and statement balances stay as free-form decimal inputs because they can be negative.
- Avoid the font's tabular figures (`tnum`): Schibsted Grotesk also makes `.` and `,` tabular-width, which looks broken.

## Design

Ledger-paper look. Tokens are in `:root` in `static/css/app.css`, with a dark variant under `prefers-color-scheme`. Pale green ledger paper, blue-black ink, accounting red for payments, bookkeeper green for verified. The accountant's double rule (`border: double`) marks final totals and draws in when a reconciliation balances. Mobile uses two-line rows, a FAB and a full-screen sheet. At 900px and up it switches to a columnar register (Payment / Deposit / Balance) with a side panel. Shortcuts: `n` new, `/` search, `Esc` close.
