# Reconcile

A personal check register for one checking account. Enter transactions by hand, tick them off as they clear the bank, and reconcile each statement down to $0.00.

Built for phone use as an installed PWA, with a full columnar register on desktop.

## Features

- **Register**: newest first, with a running balance on every row, plus **Balance** (everything, future-dated included) and **Verified** (cleared) totals. A "Today" divider separates future-dated items.
- **Tap to verify**: the circle on each row toggles verified.
- **Search and filter**: by payee (substring, case-insensitive) and/or amount. Amounts match partially: `45` finds $45.00 to $45.99, `20.0` finds $20.00 to $20.09, and `20.08` is exact. Ranges work too (`10-50` is $10.00 to $50.99; also `100-` and `-20`). Also All / Payments / Deposits / Unverified. Filters live in the URL.
- **Payee autocomplete**: suggests past payees by prefix, then by how often they're used. It never fills in an amount.
- **Reconcile a statement**: enter the closing date and ending balance, then tick items until the difference is $0.00 and finish. Finished items become *reconciled*: locked, hidden from the register by default, and editable only after confirming.
- **CSV export** from the menu, on demand only.
- All text input is trimmed before it's saved.
- Light/dark follows the system setting.

## Development

Requires [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run pytest
uv run uvicorn app.main:app --reload --reload-dir app --reload-dir static --reload-dir templates \
  --reload-include '*.css' --reload-include '*.js' --reload-include '*.html'
```

Open http://localhost:8000. On first run it asks for the account name and opening balance.

The database defaults to `./data/reconcile.db`. Override it with `RECONCILE_DB_PATH`.

## Deployment

The image is built on the dev machine (arm64) for amd64 and pushed to Docker Hub as `docker.io/xadamz23/reconcile`. Image tags follow git tags.

### Release

```sh
git tag v0.1.0 && git push origin v0.1.0
scripts/release.sh
```

`release.sh` refuses to run with uncommitted changes, or if HEAD isn't exactly at a tag. It runs the tests, then pushes `:<tag>` and `:latest`.

### Prod server

Put `compose.yaml` on the server with a `.env` next to it:

```sh
TZ=America/Chicago
RECONCILE_TAG=v0.1.0
```

Then deploy or update with:

```sh
docker compose pull && docker compose up -d
```

To roll back, set `RECONCILE_TAG` to an older tag and run the same command. If the Hub repo is private, run `docker login` on the server once.

- The SQLite DB lives in the `reconcile-data` volume (`/data/reconcile.db` in the container). Back that up.
- The app listens on `127.0.0.1:8000` only, for cloudflared on the same host. Change the port with `RECONCILE_PORT`.
- The container runs read-only as uid 1000, with no capabilities and rotated logs.
- `TZ` sets what "today" means (the default date and the Today divider). It defaults to UTC.
- The app has no auth of its own. It's meant to sit behind Cloudflare Zero Trust / Access. The manifest is requested with `crossorigin="use-credentials"` so PWA install works behind Access.

## Importing from the old register app

`scripts/import_register_csv.py` imports a CSV exported from the old app. It uses only the standard library, so it runs with the prod host's `python3` directly against the DB in the Docker volume. It does a dry run unless you pass `--apply`. Rows already in the register (same date, payee, amount and check number) are skipped, so re-running is safe. Set up the opening balance in the app first.

```sh
docker compose stop
cp -a /var/lib/docker/volumes/reconcile_reconcile-data/_data ~/reconcile-backup
DB=/var/lib/docker/volumes/reconcile_reconcile-data/_data/reconcile.db
python3 import_register_csv.py export.csv "$DB"           # preview
python3 import_register_csv.py export.csv "$DB" --apply   # import
chown dockerapps:dockerapps "$DB"*
docker compose start
```

Run it as root, because `/var/lib/docker` isn't reachable by other users. Then `chown` the DB files back to the container's user (uid 1000) so the app can still write to them.

## Icons

The source artwork is `static/icons/icon.svg`, `static/icons/favicon.svg` and `scripts/maskable.svg`. The PNG and ICO files are generated from them and committed. To regenerate after editing:

```sh
uv run --no-project --with resvg-py --with pillow python scripts/make_icons.py
```
