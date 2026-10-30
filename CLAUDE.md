# Reconcile

Single-user check register PWA. FastAPI + Jinja2 + htmx + Alpine.js + SQLite (stdlib `sqlite3`, no ORM). Design spec: `docs/superpowers/specs/2026-10-18-reconcile-design.md`.

## Commands

- Tests: `uv run pytest`
- Dev server: `uv run uvicorn app.main:app --reload --reload-dir app --reload-dir static --reload-dir templates --reload-include '*.css' --reload-include '*.js' --reload-include '*.html'`
- Release (prod is amd64, dev machine is arm64): `scripts/deploy-reconcile-from-arm64.sh vX.Y.Z`. It tags and pushes the tag, builds and pushes `docker.io/xadamz23/reconcile:<tag>` (no `:latest`), then SSHes to the server and runs `update-app.sh <tag>`. That script (a copy is in `scripts/`) rewrites the server's `docker-compose.yaml` with the tag and restarts. The server runs its own copy, so re-copy it to `/home/dockerapps/reconcile/` whenever it changes. It doesn't run tests. The repo's `compose.yaml` is a generic `.env`-driven version the server doesn't use.
- Regenerate icons: `uv run --no-project --with resvg-py --with pillow python scripts/make_icons.py`

## Layout

- `app/repo.py`: **all SQL**. Routes never write SQL themselves. Text is trimmed in `TxnInput.__post_init__` / `clean_text`, which is the one place whitespace stripping happens.
- `app/money.py`: amounts are **signed integer cents** (negative = payment). Never use floats `parse_cents` caps them at `MAX_CENTS` ($9,999,999.99).
- One SQLite connection is shared by all requests. `deps.get_conn` holds `app.state.db_lock` for the whole request, so routes must get the connection only through `Depends(get_conn)`.
- `app/db.py`: migrations are an append-only list keyed by `PRAGMA user_version`. Add a new entry and never edit an old one.
- `app/assets.py`: hashes every file in `static/` at startup. Templates must reference assets via `static_url('path')`, which yields `?v=<hash>` URLs that are served `immutable`.
- `app/routes/`: one router per area. `deps.render()` injects `today`.
- `app/routes/spending.py`: the Spending page (payments per payee for a calendar period). Its bars are plain CSS (a span sized by `--w`, set server-side), with no chart library.
- `templates/_*.html` are htmx partials. `_panel.html` (the form panel) is shared by the register and reconcile pages, and `_tabbar.html` (the mobile bottom bar) and `_masthead.html` (the header, fed by `deps.masthead_context()`) by those and Spending. `templates/sw.js` is the service worker, rendered by `routes/pwa.py`.

## Gotchas

- Asset hashes are computed at startup. Without the `--reload-include` flags (which need `watchfiles`, a dev dep), CSS/JS edits keep their old hash and the browser serves the old immutable copy.
- htmx `attributesToSettle` is set to `[]` in `base.html`. The default settle step copies `class`/`style` between same-id elements and breaks Alpine's `x-show`.
- After a save, routes return `HX-Reswap: none` plus `HX-Trigger: ledger-changed, panel-close`. `#ledger` (register) and `#rec-body` (reconcile) refetch themselves on `ledger-changed`. htmx still fires `afterSwap` on these responses, so `app.js` opens the panel only on GET.
- Running balance is a window function over the **whole** ledger, applied before filters, so filtered rows still show true balances.
- The register pages its rows (`PAGE_SIZE` in `routes/register.py`). `_ledger_rows.html` ends with a placeholder that loads the next page (a `before=<date>:<id>` cursor) when it scrolls into view. The refresh after a save sends `rows=<count loaded>` so the list keeps its place. htmx merges `hx-vals` from every ancestor, even with `hx-disinherit`, so next-page requests carry `rows` too. The route ignores it when there's a cursor.
- Row-repeated icons use `sprite()` once on the page plus `icon_ref()` per row (`_icons.html`), instead of a full inline SVG on every row.
- Statuses are `unverified` → `verified` → `reconciled`. `verified` only means "ticked on the in-progress reconcile": there is no verify control in the register or the form. Only the reconcile tick, `finish_statement` and `discard_draft` change status; `update_txn` never does. A new transaction added from the reconcile screen (`/txn/new?date=…&verified=1`) starts ticked. The opening balance (`is_opening=1`) is reconciled and can't be deleted.
- The in-progress reconcile (closing date + statement balance) is a draft in `settings` (`rec_end_date`, `rec_balance_cents`). Reconcile routes read it instead of taking form fields. Finishing clears it and unticks any `verified` items dated after the closing date; discarding clears it and unticks every `verified` item.
- Cleared balance for reconciling = all reconciled + verified items dated on or before the statement's closing date.
- CSRF: middleware in `app/main.py` rejects any non-GET request that a browser marks as cross-site (`Sec-Fetch-Site`, falling back to `Origin` vs `Host`). Auth is only Cloudflare Access, so this is the one thing stopping other sites from posting forms. Requests with neither header (curl, TestClient) are allowed.
- `scripts/import_register_csv.py` is stdlib-only (it runs on the prod host, not in the venv) and writes SQL directly, against `SCHEMA_VERSION`. Bump that constant and re-check its INSERT when adding a migration.
- The transaction amount input (`data-cents`) uses cash-register entry: digits fill from the right and there's no decimal key. The sign comes from the Payment/Deposit toggle. It's handled in `app.js`. The statement balance uses `data-cents="signed"`, where typing `-` flips the sign because the balance can be negative. Phone number pads have no minus key, so a negative statement balance needs a hardware keyboard or a paste. The opening balance stays a free-form decimal input.
- The register's search line is always on desktop and opens from the tab bar on mobile. All / Payments / Deposits and Show reconciled sit in a tray behind its filter button. Show reconciled is off in the normal register and on while searching: `enter()` in `filterBar` (`app.js`) ticks it and refetches when you focus a search field or open the tray, and Esc or the desktop masthead's X clears everything and leaves search. On mobile the way out is the tab bar's Register link. The server only ever sees the checkbox, so it still defaults to off, and any filter in the URL (including `show_reconciled=true`) reloads in search mode. The button's count and the chips under the bar show what differs from the search defaults, so "Reconciled hidden" counts and reconciled being on doesn't.
- The `n` shortcut clicks the first `[data-new-txn]`: the desktop New transaction button or the tab bar's +. One of them is always hidden, but `click()` still works. Off the register, the tab bar's Search (and + on pages without a panel) link to `/#search` and `/#new`, which `app.js` acts on after the register loads and then strips from the URL.
- The viewport meta has `interactive-widget=resizes-content`: on Android the keyboard shrinks the page instead of covering it, so the fixed sheet ends above the keyboard and scrolls to the focused field. With Chrome's default (`resizes-visual`) it panned the view instead, cutting off the sheet's top with no way to scroll back. Fixed-bottom UI (the tab bar) hides while a text field has focus, or it would ride up on the keyboard. Dismissing the keyboard on Android leaves the field focused, so `app.js` blurs it when the page returns to full height.
- Avoid the font's tabular figures (`tnum`): Schibsted Grotesk also makes `.` and `,` tabular-width, which looks broken.
- At 900px and up every `main` is a CSS size container (`ledger`), and the ledger columns, filter bar and reconcile summary respond to `@container`, not the window. Size containment makes `main` the containing block for `position: fixed` descendants and its own stacking context, so put toasts, dialogs and other overlays outside `main`.

## Design

Dark glassmorphism, dark-only (no light mode). Frosted glass surfaces over a fixed "aurora" (`.aurora` in `base.html`: near-black `#070814` with three blurred sapphire, cyan and indigo glows). Tokens are in `:root` in `static/css/app.css`. Sapphire accent (a `#1d4ed8` → `#0e7490` gradient) for actions and the current tab. Payment red (`--pay`) and deposit/verified green (`--dep`) carry meaning, so the accent never stands in for either, and the Payment/Deposit pills keep red/green instead of the accent. The accountant's double rule (`border: double`) still marks final totals and draws in when a reconciliation balances. Mobile uses two-line rows inside glass cards and a full-screen sheet. The register, Spending and Reconcile pages share a bottom tab bar, a glass panel with 24px corners floating clear of the screen edges (Reconcile, Spending, a flat 44px accent-outline + ring, Search, Register: the most used on the right, under a right thumb). The active tab gets an accent pill behind its icon, and the back arrows are desktop-only. At 900px and up the form is a drawer that slides in from the right over a scrim only while it's open (the page keeps its full width), and once the register itself is 48rem wide it switches to columns (Payment / Deposit / Balance). Shortcuts: `n` new, `/` search, `Esc` closes the form, or otherwise clears and closes search.

## UI Conventions

The glass component classes live in one block ("Glass components") near the top of `app.css`. Build surfaces from them instead of writing one-off backgrounds, borders or blurs:

- `.glass`: cards, panels, filter bar, segmented-control tracks. Combine with `.card` (margins and radius) and `.card-list` (clips rows) for a list card.
- `.glass-strong`: chrome (masthead, tab bar, form sheet/drawer, menus, suggestions). Add `.is-popover` for dropdowns over text (92% instead of 60%, so text underneath doesn't show through).
- `.glass-chip`: small badges (check numbers, the Today marker).
- `.glass-input`: inputs, or a wrapper around one (`.amount-input`), which then gets the focus ring via `:focus-within`.
- `.accent`: primary buttons. Has its own `:disabled` look. The current tab and the on-switch reuse its gradient. The tab bar's + (`.tab-add`) is deliberately not `.accent`: it's an outline ring so it isn't mistaken for the current-tab pill.
- `.btn-glass`: secondary buttons. Fills with the accent on hover.
- `.pill-group.glass` + `.pill-radio` labels (radio inside, visually hidden): radio groups (spending periods, Payment/Deposit). It scrolls sideways when too wide.
- `.seg` + `.pill-radio` labels: a full-width segmented control with square-ish corners (register kinds).
- `.glass-card`: hover lift for clickable grid cards. There are none yet, so don't use it on list rows.

Rules: write only the standard `backdrop-filter`, never `-webkit-backdrop-filter`. Don't put `backdrop-filter` on per-row elements (put it on the containing card), because it's costly on long lists. `body` has no background on purpose: it would paint over the aurora (`z-index: -1`), and `html` carries `--bg`. Keep text at `--text-faint` (#8a92ad) or brighter so it passes AA on glass. The form panel and suggestion list are deliberately more opaque than the other chrome because content sits right behind them. There's no CSS build step: the classes take effect as soon as `app.css` changes.
