#!/usr/bin/env python3
"""
createDb.py — Create FRT database tables in a MariaDB / MySQL database.

Creates three tables:
  dbo_frn          — one row per parent FRN
  dbo_frnSubEntry  — calibre/shots/barrel sub-entries (child of dbo_frn)
  dbo_frnNote      — notes sub-section bullets (child of dbo_frn)

Cross-references are stored as a JSON array in dbo_frn.cross_references (LONGTEXT).

Usage:
  python createDb.py --host localhost --database FRT
  python createDb.py --host localhost --database FRT --user root --password secret
  python createDb.py --host myserver --port 3307 --database FRT --user admin --password secret
"""

import argparse
import sys
import time

try:
    import mysql.connector
    from mysql.connector import Error as MySQLError
except ImportError:
    print("Error: mysql-connector-python is required.  Install with:  pip install mysql-connector-python")
    sys.exit(1)


_DDL = [
    # ------------------------------------------------------------------ dbo_frn
    (
        "dbo_frn",
        """
        create table if not exists dbo_frn (
            frn                  varchar(50)   not null,
            make                 varchar(255)  not null default '',
            model                varchar(255)  not null default '',
            manufacturer         varchar(255)  not null default '',
            level                varchar(255)  not null default '',
            type                 varchar(255)  not null default '',
            action               varchar(255)  not null default '',
            country              varchar(255)  not null default '',
            legal_classification varchar(50)   not null default '',
            serial_numbering     longtext      null,
            year_dates           varchar(255)  not null default '',
            importer             varchar(255)  not null default '',
            `fullText`           longtext      null,
            primary key (frn)
        ) engine=InnoDB default charset=utf8mb4
        """,
    ),

    # --------------------------------------------------------- dbo_frnSubEntry
    (
        "dbo_frnSubEntry",
        """
        create table if not exists dbo_frnSubEntry (
            id                   int           not null auto_increment,
            frn                  varchar(50)   not null default '',
            sub_frn              varchar(50)   not null default '',
            calibre              varchar(255)  not null default '',
            shots                varchar(50)   not null default '',
            barrel_length        varchar(50)   not null default '',
            legal_classification varchar(50)   not null default '',
            `fullText`           longtext      null,
            primary key (id),
            constraint FK_dbo_frnSubEntry_frn foreign key (frn)
                references dbo_frn (frn)
        ) engine=InnoDB default charset=utf8mb4
        """,
    ),

    # -------------------------------------------------------------- dbo_frnNote
    (
        "dbo_frnNote",
        """
        create table if not exists dbo_frnNote (
            id           int          not null auto_increment,
            frn          varchar(50)  not null default '',
            note_key     varchar(255) not null default '',
            bullet_index int          not null default 0,
            note_value   longtext     null,
            `fullText`   longtext     null,
            primary key (id),
            constraint FK_dbo_frnNote_frn foreign key (frn)
                references dbo_frn (frn)
        ) engine=InnoDB default charset=utf8mb4
        """,
    ),

    # ------------------------------------------------------- dbo_frnCrossReference
    (
        "dbo_frnCrossReference",
        """
        create table if not exists dbo_frnCrossReference (
            id          int          not null auto_increment,
            frn         varchar(50)  not null default '',
            ref_frn     varchar(50)  not null default '',
            description varchar(255) not null default '',
            `fullText`  longtext     null,
            primary key (id),
            constraint FK_dbo_frnCrossReference_frn foreign key (frn)
                references dbo_frn (frn)
        ) engine=InnoDB default charset=utf8mb4
        """,
    ),

    # ---------------------------------------------------------------- dbo_frnPdf
    (
        "dbo_frnPdf",
        """
        create table if not exists dbo_frnPdf (
            id     int          not null auto_increment,
            frn    varchar(50)  not null default '',
            pdf64  longtext     null,
            primary key (id),
            constraint FK_dbo_frnPdf_frn foreign key (frn)
                references dbo_frn (frn)
        ) engine=InnoDB default charset=utf8mb4
        """,
    ),
]

# Drop in reverse FK order (children before parent) so constraints don't block the drop
_DROP = [
    "drop table if exists dbo_frnPdf",
    "drop table if exists dbo_frnCrossReference",
    "drop table if exists dbo_frnNote",
    "drop table if exists dbo_frnSubEntry",
    "drop table if exists dbo_frn",
]

_FULLTEXTIFY = """
create function fulltextify(input longtext charset utf8mb4)
returns longtext charset utf8mb4
deterministic
begin
    declare cleaned longtext charset utf8mb4;
    declare extras  longtext charset utf8mb4 default '';
    declare tok     longtext charset utf8mb4;
    declare n       int default 1;

    -- strip common special chars (dashes are handled below)
    set cleaned = replace(input,    '(',  '');
    set cleaned = replace(cleaned,  ')',  '');
    set cleaned = replace(cleaned,  '[',  '');
    set cleaned = replace(cleaned,  ']',  '');
    set cleaned = replace(cleaned,  '{',  '');
    set cleaned = replace(cleaned,  '}',  '');
    set cleaned = replace(cleaned,  '.',  '');
    set cleaned = replace(cleaned,  ',',  '');
    set cleaned = replace(cleaned,  ';',  '');
    set cleaned = replace(cleaned,  ':',  '');
    set cleaned = replace(cleaned,  '/',  '');
    set cleaned = replace(cleaned,  '|',  '');
    set cleaned = replace(cleaned,  '!',  '');
    set cleaned = replace(cleaned,  '?',  '');
    set cleaned = replace(cleaned,  '@',  '');
    set cleaned = replace(cleaned,  '#',  '');
    set cleaned = replace(cleaned,  '$',  '');
    set cleaned = replace(cleaned,  '%',  '');
    set cleaned = replace(cleaned,  '^',  '');
    set cleaned = replace(cleaned,  '&',  '');
    set cleaned = replace(cleaned,  '*',  '');
    set cleaned = replace(cleaned,  '+',  '');
    set cleaned = replace(cleaned,  '=',  '');
    set cleaned = replace(cleaned,  '~',  '');
    set cleaned = replace(cleaned,  '`',  '');
    set cleaned = replace(cleaned,  '"',  '');
    set cleaned = replace(cleaned,  '''', '');
    set cleaned = replace(cleaned,  '<',  '');
    set cleaned = replace(cleaned,  '>',  '');
    set cleaned = trim(cleaned);

    if cleaned is null or char_length(cleaned) = 0 then
        return '';
    end if;

    -- every dashed word is also indexed with its dashes removed, so ST-22 is
    -- stored as both STDSH22 and ST22 (a search for ST22 then finds either form)
    dashed: loop
        set tok = regexp_substr(cleaned, '[^[:space:]]*-[^[:space:]]*', 1, n);
        if tok is null then
            leave dashed;
        end if;
        set tok = replace(tok, '-', '');
        if char_length(tok) > 0 then
            set extras = concat(extras, ' ', tok);
        end if;
        set n = n + 1;
    end loop;

    set cleaned = concat(replace(cleaned, '-', 'DSH'), extras);

    -- forward and reversed so both directions are searchable
    set cleaned = concat(cleaned, ' ', reverse(cleaned));

    -- prefix 1-2 character words with __ so they reach innodb_ft_min_token_size (3)
    return regexp_replace(cleaned, '(?<![[:alnum:]_])([[:alnum:]]{1,2})(?![[:alnum:]_])', '__$1');
end
"""

_INDEXES = [
    ("IX_dbo_frn_make",                    "dbo_frn",              "create index          IX_dbo_frn_make                    on dbo_frn (make)"),
    ("IX_dbo_frn_manufacturer",            "dbo_frn",              "create index          IX_dbo_frn_manufacturer            on dbo_frn (manufacturer)"),
    ("IX_dbo_frn_type",                    "dbo_frn",              "create index          IX_dbo_frn_type                    on dbo_frn (type)"),
    ("IX_dbo_frn_action",                  "dbo_frn",              "create index          IX_dbo_frn_action                  on dbo_frn (action)"),
    ("IX_dbo_frn_country",                 "dbo_frn",              "create index          IX_dbo_frn_country                 on dbo_frn (country)"),
    ("IX_dbo_frn_legal_classification",    "dbo_frn",              "create index          IX_dbo_frn_legal_classification    on dbo_frn (legal_classification)"),
    ("IX_dbo_frn_model",                   "dbo_frn",              "create index          IX_dbo_frn_model                   on dbo_frn (model)"),
    ("IX_dbo_frnSubEntry_frn",             "dbo_frnSubEntry",      "create index          IX_dbo_frnSubEntry_frn             on dbo_frnSubEntry (frn)"),
    ("IX_dbo_frnSubEntry_sub_frn",         "dbo_frnSubEntry",      "create index          IX_dbo_frnSubEntry_sub_frn         on dbo_frnSubEntry (sub_frn)"),
    ("IX_dbo_frnSubEntry_calibre",         "dbo_frnSubEntry",      "create index          IX_dbo_frnSubEntry_calibre         on dbo_frnSubEntry (calibre)"),
    ("IX_dbo_frnSubEntry_barrel_length",   "dbo_frnSubEntry",      "create index          IX_dbo_frnSubEntry_barrel_length   on dbo_frnSubEntry (barrel_length)"),
    ("IX_dbo_frnSubEntry_legal_class",     "dbo_frnSubEntry",      "create index          IX_dbo_frnSubEntry_legal_class     on dbo_frnSubEntry (legal_classification)"),
    ("IX_dbo_frnNote_frn",                 "dbo_frnNote",          "create index          IX_dbo_frnNote_frn                 on dbo_frnNote (frn)"),
    ("IX_dbo_frnNote_frn_key",             "dbo_frnNote",          "create index          IX_dbo_frnNote_frn_key             on dbo_frnNote (frn, note_key)"),
    ("IX_dbo_frnCrossReference_frn",       "dbo_frnCrossReference","create index          IX_dbo_frnCrossReference_frn       on dbo_frnCrossReference (frn)"),
    ("IX_dbo_frnPdf_frn",                 "dbo_frnPdf",           "create index          IX_dbo_frnPdf_frn                  on dbo_frnPdf (frn)"),
    ("FTI_dbo_frn_fullText",               "dbo_frn",              "create fulltext index FTI_dbo_frn_fullText               on dbo_frn (`fullText`)"),
    ("FTI_dbo_frnSubEntry_fullText",       "dbo_frnSubEntry",      "create fulltext index FTI_dbo_frnSubEntry_fullText       on dbo_frnSubEntry (`fullText`)"),
    ("FTI_dbo_frnNote_fullText",           "dbo_frnNote",          "create fulltext index FTI_dbo_frnNote_fullText           on dbo_frnNote (`fullText`)"),
    ("FTI_dbo_frnCrossReference_fullText", "dbo_frnCrossReference","create fulltext index FTI_dbo_frnCrossReference_fullText on dbo_frnCrossReference (`fullText`)"),
]


# Expressions that feed each table's fullText column, with {r} as the row
# prefix ("new." inside a trigger, empty in rebuild_fulltext()).
_FULLTEXT_SOURCES = {
    "dbo_frn": [
        "{r}frn", "{r}make", "{r}model", "{r}manufacturer",
        "{r}level", "{r}type", "{r}action", "{r}country",
        "{r}legal_classification", "{r}year_dates", "{r}importer",
    ],
    "dbo_frnSubEntry": [
        "{r}frn", "{r}sub_frn", "{r}calibre",
        "{r}shots", "{r}barrel_length", "{r}legal_classification",
    ],
    "dbo_frnNote": [
        "{r}note_value", "cast({r}bullet_index as char)",
    ],
    "dbo_frnCrossReference": [
        "{r}ref_frn", "{r}description",
    ],
}

# importIntoDb.py sets this session variable so the triggers skip rows it
# inserts; it fills fullText for every row afterwards with rebuild_fulltext().
_SKIP_VAR = "@frt_skip_fulltext"


def _fulltext_expr(table: str, row: str = "") -> str:
    cols = ", ".join(c.format(r=row) for c in _FULLTEXT_SOURCES[table])
    return f"fulltextify(concat_ws(' ', {cols}))"


_TRIGGERS = [
    (
        f"trg_{table}_{suffix}",
        f"""
        create trigger trg_{table}_{suffix}
        before {event} on {table} for each row
        begin
            if {_SKIP_VAR} is null then
                set new.`fullText` = {_fulltext_expr(table, "new.")};
            end if;
        end
        """,
    )
    for table in _FULLTEXT_SOURCES
    for suffix, event in (("bi", "insert"), ("bu", "update"))
]


def rebuild_fulltext(conn, database: str) -> None:
    """Recompute fullText for every row, then rebuild each FULLTEXT index from scratch.

    The index is dropped first so the update doesn't maintain it row by row. The
    triggers are skipped for this session so each value is computed only once.
    """
    cursor = conn.cursor()
    cursor.execute(f"set {_SKIP_VAR} = 1")
    print("Rebuilding fulltext:")
    for table in _FULLTEXT_SOURCES:
        index = f"FTI_{table}_fullText"
        print(f"  fulltext {table}:", end=" ", flush=True)
        started = time.monotonic()
        if _index_exists(cursor, database, index, table):
            cursor.execute(f"alter table {table} drop index {index}")
        cursor.execute(f"update {table} set `fullText` = {_fulltext_expr(table)}")
        updated = cursor.rowcount
        conn.commit()
        cursor.execute(f"create fulltext index {index} on {table} (`fullText`)")
        print(f"{updated:,} updated ({time.monotonic() - started:.1f}s)", flush=True)
    cursor.execute(f"set {_SKIP_VAR} = null")


def _index_exists(cursor, database: str, index_name: str, table_name: str) -> bool:
    cursor.execute(
        """
        select 1 from information_schema.statistics
        where table_schema = %s
          and table_name   = %s
          and index_name   = %s
        limit 1
        """,
        (database, table_name, index_name),
    )
    return cursor.fetchone() is not None


def create_tables(host: str, port: int, database: str, user: str, password: str) -> None:
    print("Connecting...", end=" ", flush=True)
    conn = mysql.connector.connect(
        host=host,
        port=port,
        database=database,
        user=user,
        password=password,
        charset="utf8mb4",
    )
    print("connected")

    try:
        cursor = conn.cursor()

        print("Dropping function fulltextify ...", end=" ", flush=True)
        cursor.execute("drop function if exists fulltextify")
        conn.commit()
        print("ok")

        print("Dropping tables:")
        for ddl in _DROP:
            table = ddl.split()[-1]
            print(f"  {table} ...", end=" ", flush=True)
            cursor.execute(ddl)
            conn.commit()
            print("ok")

        print("Creating tables:")
        for name, ddl in _DDL:
            print(f"  {name} ...", end=" ", flush=True)
            cursor.execute(ddl)
            conn.commit()
            print("ok")

        print("Creating indexes:")
        for idx_name, table_name, ddl in _INDEXES:
            print(f"  {idx_name} ...", end=" ", flush=True)
            if _index_exists(cursor, database, idx_name, table_name):
                print("already exists")
            else:
                cursor.execute(ddl)
                conn.commit()
                print("ok")

        print("Creating function fulltextify ...", end=" ", flush=True)
        cursor.execute(_FULLTEXTIFY)
        conn.commit()
        print("ok")

        print("Creating triggers:")
        for name, ddl in _TRIGGERS:
            print(f"  {name} ...", end=" ", flush=True)
            cursor.execute(ddl)
            conn.commit()
            print("ok")

    finally:
        conn.close()

    print("\nDone.")
    print("\nTable summary:")
    print("  dbo_frn               — one row per parent FRN")
    print("                          PK: frn")
    print("  dbo_frnSubEntry       — calibre/shots/barrel rows")
    print("                          PK: id  FK: frn → dbo_frn")
    print("  dbo_frnNote           — notes bullets")
    print("                          PK: id  FK: frn → dbo_frn")
    print("                          note_key (camelCase), bullet_index, note_value")
    print("  dbo_frnCrossReference — cross-referenced FRNs")
    print("                          PK: id  FK: frn → dbo_frn")
    print("  dbo_frnPdf            — base64-encoded FRN PDFs")
    print("                          PK: id  FK: frn → dbo_frn")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create FRT database tables in a MariaDB / MySQL database.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python createDb.py --host localhost --database FRT
  python createDb.py --host localhost --database FRT --user root --password secret
  python createDb.py --host myserver --port 3307 --database FRT --user admin --password secret
        """,
    )
    parser.add_argument("--host",     "-s", default="localhost", help="Database host (default: localhost)")
    parser.add_argument("--port",           type=int, default=3306, help="Database port (default: 3306)")
    parser.add_argument("--database", "-d", required=True,       help="Target database name")
    parser.add_argument("--user",     "-u", default="root",      help="Database user (default: root)")
    parser.add_argument("--password", "-p", default="",          help="Database password (default: empty)")

    args = parser.parse_args()

    try:
        create_tables(args.host, args.port, args.database, args.user, args.password)
    except MySQLError as exc:
        print(f"\nDatabase error: {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
