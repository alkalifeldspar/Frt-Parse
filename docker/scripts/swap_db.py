#!/usr/bin/env python3
"""
swap_db.py — Zero-downtime promotion of a freshly imported FRT database.

The ingest pipeline builds the new data in a *staging* database. This script
then moves it into the *live* database with a single `RENAME TABLE` statement,
which MySQL executes atomically: readers of the live database see either the
complete old data set or the complete new one, never a missing or half-loaded
table. The tables that were live are kept in a *previous* database so they can
be rolled back.

Commands:
  promote   validate staging, then swap  staging → live → previous
  rollback  swap previous ↔ live
  repair    recreate the fulltextify function, and any missing triggers, in live

MySQL refuses to move a table that has triggers into another database
(ER_TRG_IN_WRONG_SCHEMA), so triggers are dropped before the rename and
recreated on the live tables afterwards. Table, trigger and function
definitions are imported from createDb.py so there is one source of truth.

The last line printed on success is `RESULT {json}` with row counts.
Exit codes: 0 ok, 1 error, 2 validation failed (staging left in place).
"""

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.environ.get("FRT_SCRIPTS_DIR", os.path.dirname(os.path.abspath(__file__))))

import mysql.connector  # noqa: E402
import createDb  # noqa: E402

TABLES = [name for name, _ in createDb._DDL]   # parent first: dbo_frn, then children
TRIGGERS = createDb._TRIGGERS                  # [(name, ddl), ...]
_IDENT = re.compile(r"^[A-Za-z0-9_]+$")


class ValidationError(Exception):
    pass


def q(name: str) -> str:
    """Quote a database/table identifier after validating it."""
    if not _IDENT.match(name):
        raise ValueError(f"Invalid identifier: {name!r}")
    return f"`{name}`"


def log(msg: str) -> None:
    print(msg, flush=True)


def connect(args):
    conn = mysql.connector.connect(
        host=args.host, port=args.port, user=args.user, password=args.password,
        charset="utf8mb4", autocommit=True,
    )
    cur = conn.cursor()
    # Without a bound, RENAME TABLE waits up to a year for a long-running
    # query to release its metadata lock. Fail fast instead.
    cur.execute("set session lock_wait_timeout = %s", (args.lock_wait_timeout,))
    return conn, cur


def db_exists(cur, db: str) -> bool:
    cur.execute("select 1 from information_schema.schemata where schema_name = %s", (db,))
    return cur.fetchone() is not None


def existing_tables(cur, db: str) -> list:
    """FRT tables present in `db`, in TABLES order."""
    cur.execute(
        "select table_name from information_schema.tables "
        "where table_schema = %s and table_type = 'BASE TABLE'",
        (db,),
    )
    present = {row[0] for row in cur.fetchall()}
    return [t for t in TABLES if t in present]


def row_counts(cur, db: str) -> dict:
    counts = {}
    for t in existing_tables(cur, db):
        cur.execute(f"select count(*) from {q(db)}.{q(t)}")
        counts[t] = cur.fetchone()[0]
    return counts


def drop_triggers(cur, db: str) -> None:
    if not db_exists(cur, db):
        return
    for name, _ in TRIGGERS:
        cur.execute(f"drop trigger if exists {q(db)}.{q(name)}")


def ensure_live_objects(cur, live: str) -> None:
    """Make sure fulltextify() and every trigger exist on the live tables."""
    if existing_tables(cur, live) != TABLES:
        return
    cur.execute(f"use {q(live)}")
    # Always recreate: the function stays behind in each database when tables are
    # renamed, so live could otherwise keep an older definition than staging used.
    cur.execute("drop function if exists fulltextify")
    cur.execute(createDb._FULLTEXTIFY)
    log("  function fulltextify in place")
    cur.execute(
        "select trigger_name from information_schema.triggers where trigger_schema = %s",
        (live,),
    )
    present = {row[0] for row in cur.fetchall()}
    for name, ddl in TRIGGERS:
        if name not in present:
            cur.execute(ddl)
    log("  triggers in place")


def analyze(cur, db: str) -> None:
    """Refresh InnoDB statistics so information_schema sizes are current."""
    tables = existing_tables(cur, db)
    if tables:
        cur.execute("analyze table " + ", ".join(f"{q(db)}.{q(t)}" for t in tables))
        cur.fetchall()


def atomic_rename(cur, phases) -> None:
    """Run one RENAME TABLE statement built from (src_db, dst_db, tables) phases.

    Phases run left to right inside the single statement. Emptying a database
    before anything moves into it keeps the per-database foreign key
    constraint names from colliding.
    """
    pairs = [
        f"{q(src)}.{q(t)} to {q(dst)}.{q(t)}"
        for src, dst, tables in phases
        for t in tables
    ]
    if pairs:
        cur.execute("rename table " + ", ".join(pairs))


def validate(cur, staging: str, live: str, min_ratio: float) -> tuple:
    if not db_exists(cur, staging):
        raise ValidationError(f"Staging database {staging} does not exist")
    missing = [t for t in TABLES if t not in existing_tables(cur, staging)]
    if missing:
        raise ValidationError(f"Staging is missing tables: {', '.join(missing)}")

    staged = row_counts(cur, staging)
    log("  staging rows: " + ", ".join(f"{t}={n:,}" for t, n in staged.items()))
    if staged["dbo_frn"] == 0:
        raise ValidationError("Staging dbo_frn is empty")

    current = row_counts(cur, live) if db_exists(cur, live) else {}
    if current:
        log("  live rows:    " + ", ".join(f"{t}={n:,}" for t, n in current.items()))
    live_frn = current.get("dbo_frn", 0)
    if live_frn and staged["dbo_frn"] < live_frn * min_ratio:
        raise ValidationError(
            f"Staging has {staged['dbo_frn']:,} FRNs vs {live_frn:,} live "
            f"(< {min_ratio:.0%}). Refusing to swap; staging kept for inspection. "
            f"Lower MIN_ROW_RATIO to accept it."
        )
    if staged.get("dbo_frnPdf", 0) == 0:
        log("  warning: staging dbo_frnPdf is empty")
    return staged, current


def cmd_promote(args) -> dict:
    conn, cur = connect(args)
    try:
        log(f"Validating {args.staging} ...")
        staged, current = validate(cur, args.staging, args.live, args.min_ratio)

        cur.execute(f"create database if not exists {q(args.live)} default character set utf8mb4")
        live_tables = existing_tables(cur, args.live)

        if live_tables:
            cur.execute(f"drop database if exists {q(args.previous)}")
            cur.execute(f"create database {q(args.previous)} default character set utf8mb4")

        log("Dropping triggers for the move ...")
        drop_triggers(cur, args.staging)
        drop_triggers(cur, args.live)

        log(f"Swapping {args.staging} → {args.live}"
            + (f" (current tables → {args.previous})" if live_tables else "") + " ...")
        try:
            atomic_rename(cur, [
                (args.live, args.previous, live_tables),
                (args.staging, args.live, TABLES),
            ])
        finally:
            # Put triggers back on whatever is live now, even if the rename failed.
            ensure_live_objects(cur, args.live)
        log("SWAPPED")

        cur.execute(f"drop database if exists {q(args.staging)}")
        if not args.keep_previous and live_tables:
            log(f"Dropping {args.previous} (KEEP_PREVIOUS is off) ...")
            cur.execute(f"drop database if exists {q(args.previous)}")
        analyze(cur, args.live)
        if args.keep_previous:
            analyze(cur, args.previous)

        return {"live": staged, "previous": current if args.keep_previous else {}}
    finally:
        conn.close()


def cmd_rollback(args) -> dict:
    conn, cur = connect(args)
    tmp = f"{args.live}_swap"
    try:
        missing = [t for t in TABLES if t not in existing_tables(cur, args.previous)]
        if missing:
            raise ValidationError(f"No complete previous version in {args.previous}")

        live_tables = existing_tables(cur, args.live)
        cur.execute(f"drop database if exists {q(tmp)}")
        cur.execute(f"create database {q(tmp)} default character set utf8mb4")
        drop_triggers(cur, args.live)
        drop_triggers(cur, args.previous)

        log(f"Swapping {args.previous} ↔ {args.live} ...")
        try:
            atomic_rename(cur, [
                (args.live, tmp, live_tables),
                (args.previous, args.live, TABLES),
                (tmp, args.previous, live_tables),
            ])
        finally:
            ensure_live_objects(cur, args.live)
        log("SWAPPED")
        cur.execute(f"drop database if exists {q(tmp)}")
        analyze(cur, args.live)
        analyze(cur, args.previous)
        return {"live": row_counts(cur, args.live), "previous": row_counts(cur, args.previous)}
    finally:
        conn.close()


def cmd_repair(args) -> dict:
    conn, cur = connect(args)
    try:
        log(f"Checking function and triggers in {args.live} ...")
        ensure_live_objects(cur, args.live)
        return {}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Zero-downtime swap of FRT databases.")
    parser.add_argument("command", choices=["promote", "rollback", "repair"])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3306)
    parser.add_argument("--user", default="root")
    parser.add_argument("--password", default=os.environ.get("MYSQL_ROOT_PASSWORD", ""))
    parser.add_argument("--live", required=True)
    parser.add_argument("--staging")
    parser.add_argument("--previous")
    parser.add_argument("--min-ratio", type=float, default=0.9,
                        help="Minimum staging/live dbo_frn row ratio for promote (default 0.9)")
    parser.add_argument("--keep-previous", action="store_true",
                        help="Keep the replaced tables in --previous for rollback")
    parser.add_argument("--lock-wait-timeout", type=int, default=120,
                        help="Seconds to wait for metadata locks during the rename")
    args = parser.parse_args()

    if args.command == "promote" and not (args.staging and args.previous):
        parser.error("promote needs --staging and --previous")
    if args.command == "rollback" and not args.previous:
        parser.error("rollback needs --previous")

    try:
        result = {"promote": cmd_promote, "rollback": cmd_rollback, "repair": cmd_repair}[args.command](args)
    except ValidationError as exc:
        log(f"Validation failed: {exc}")
        sys.exit(2)
    except mysql.connector.Error as exc:
        log(f"Database error: {exc}")
        sys.exit(1)
    log("RESULT " + json.dumps(result))


if __name__ == "__main__":
    main()
