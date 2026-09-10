# Database Migrations (Flask-Migrate)

This app now uses Flask-Migrate (built on Alembic) to change the database
schema safely — without deleting your SQLite `.db` file or touching your
live Postgres instance destructively.

This coexists with the older "ad-hoc column upgrade" block near the bottom
of `app.py` (the one that does `ALTER TABLE ... ADD COLUMN` in a loop on
every startup). That block is left in place so your **existing** local
SQLite file and the **existing** live Postgres database — both of which
predate Flask-Migrate — keep working exactly as before. From now on,
**new** schema changes should go through a migration instead of being
added to that block.

## One-time setup (do this once, locally)

The `flask` CLI needs to know where your app object lives. Set this once
per terminal session (or add it to your `.env` file, since this project
already uses `python-dotenv`):

```bash
export FLASK_APP=app.py
```

Then, from your project root, with your virtual environment active:

```bash
pip install -r requirements.txt
flask db init
```

This creates a `migrations/` folder. Commit it to git — it's part of your
project, not a build artifact.

Then generate your first migration. Because your database already has all
the current tables/columns (created by `db.create_all()` and the old
ad-hoc block), you have two options:

**Option A — Start migrations from "now" (recommended):**
```bash
flask db stamp head
```
This tells Alembic "the database is already up to date as of the current
models.py — start tracking changes from here," without trying to
re-create anything that already exists. Do this against BOTH your local
SQLite database and your live Postgres database (using the same
`DATABASE_URL` env var Postgres already uses) once.

**Option B — Generate a migration and review it:**
```bash
flask db migrate -m "baseline"
```
Then open the generated file in `migrations/versions/` and check it
doesn't try to drop/recreate your existing tables (since `db.create_all()`
already made them, Alembic may generate an empty or near-empty migration —
that's expected and fine). Apply it with `flask db upgrade`.

Most people should just use Option A.

## Day-to-day: making a schema change

1. Edit `models.py` as usual (add a column, add a table, etc.).
2. Generate a migration from the diff:
   ```bash
   flask db migrate -m "short description of the change"
   ```
3. **Open the generated file in `migrations/versions/` and read it.**
   Alembic is good but not perfect — double-check it's only doing what you
   expect (especially for column type changes or drops).
4. Apply it locally:
   ```bash
   flask db upgrade
   ```
5. Commit the new migration file to git along with your `models.py` change.
6. On deploy (Render), run `flask db upgrade` against the live database
   before/as part of your deploy step — e.g. as a Render "pre-deploy
   command," or manually via the Render shell the first time you set this
   up. This applies the same migration to production without deleting any
   data.

## Rolling back

```bash
flask db downgrade -1
```
Reverts the most recent migration. Use with care on production data.

## Why this matters

Before this change, growing the schema meant either manually running
`ALTER TABLE` statements, or (worse) deleting the `.db` file and letting
`db.create_all()` rebuild it from scratch — which throws away all your
data. Migrations let you version schema changes the same way you version
code changes, and apply them without data loss.
