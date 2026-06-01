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

    -- replace dash with token, then strip common special chars
    set cleaned = replace(input,    '-',  'DSH');
    set cleaned = replace(cleaned,  '(',  '');
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

    if char_length(cleaned) = 0 then
        return '';
    end if;

    -- return forward and reversed so both directions are searchable
    return concat(cleaned, ' ', reverse(cleaned));
end
"""

_INDEXES = [
    ("IX_dbo_frnSubEntry_frn",             "dbo_frnSubEntry",      "create index          IX_dbo_frnSubEntry_frn             on dbo_frnSubEntry (frn)"),
    ("IX_dbo_frnNote_frn",                 "dbo_frnNote",          "create index          IX_dbo_frnNote_frn                 on dbo_frnNote (frn)"),
    ("IX_dbo_frnNote_frn_key",             "dbo_frnNote",          "create index          IX_dbo_frnNote_frn_key             on dbo_frnNote (frn, note_key)"),
    ("IX_dbo_frnCrossReference_frn",       "dbo_frnCrossReference","create index          IX_dbo_frnCrossReference_frn       on dbo_frnCrossReference (frn)"),
    ("IX_dbo_frnPdf_frn",                 "dbo_frnPdf",           "create index          IX_dbo_frnPdf_frn                  on dbo_frnPdf (frn)"),
    ("FTI_dbo_frn_fullText",               "dbo_frn",              "create fulltext index FTI_dbo_frn_fullText               on dbo_frn (`fullText`)"),
    ("FTI_dbo_frnSubEntry_fullText",       "dbo_frnSubEntry",      "create fulltext index FTI_dbo_frnSubEntry_fullText       on dbo_frnSubEntry (`fullText`)"),
    ("FTI_dbo_frnNote_fullText",           "dbo_frnNote",          "create fulltext index FTI_dbo_frnNote_fullText           on dbo_frnNote (`fullText`)"),
    ("FTI_dbo_frnCrossReference_fullText", "dbo_frnCrossReference","create fulltext index FTI_dbo_frnCrossReference_fullText on dbo_frnCrossReference (`fullText`)"),
]


_TRIGGERS = [
    # ------------------------------------------------------------------ dbo_frn
    (
        "trg_dbo_frn_bi",
        """
        create trigger trg_dbo_frn_bi
        before insert on dbo_frn for each row
        begin
            set new.`fullText` = fulltextify(concat_ws(' ',
                new.frn, new.make, new.model, new.manufacturer,
                new.level, new.type, new.action, new.country,
                new.legal_classification, new.year_dates, new.importer
            ));
        end
        """,
    ),
    (
        "trg_dbo_frn_bu",
        """
        create trigger trg_dbo_frn_bu
        before update on dbo_frn for each row
        begin
            set new.`fullText` = fulltextify(concat_ws(' ',
                new.frn, new.make, new.model, new.manufacturer,
                new.level, new.type, new.action, new.country,
                new.legal_classification, new.year_dates, new.importer
            ));
        end
        """,
    ),

    # --------------------------------------------------------- dbo_frnSubEntry
    (
        "trg_dbo_frnSubEntry_bi",
        """
        create trigger trg_dbo_frnSubEntry_bi
        before insert on dbo_frnSubEntry for each row
        begin
            set new.`fullText` = fulltextify(concat_ws(' ',
                new.frn, new.sub_frn, new.calibre,
                new.shots, new.barrel_length, new.legal_classification
            ));
        end
        """,
    ),
    (
        "trg_dbo_frnSubEntry_bu",
        """
        create trigger trg_dbo_frnSubEntry_bu
        before update on dbo_frnSubEntry for each row
        begin
            set new.`fullText` = fulltextify(concat_ws(' ',
                new.frn, new.sub_frn, new.calibre,
                new.shots, new.barrel_length, new.legal_classification
            ));
        end
        """,
    ),

    # -------------------------------------------------------------- dbo_frnNote
    (
        "trg_dbo_frnNote_bi",
        """
        create trigger trg_dbo_frnNote_bi
        before insert on dbo_frnNote for each row
        begin
            set new.`fullText` = fulltextify(concat_ws(' ',
                new.note_value, cast(new.bullet_index as char)
            ));
        end
        """,
    ),
    (
        "trg_dbo_frnNote_bu",
        """
        create trigger trg_dbo_frnNote_bu
        before update on dbo_frnNote for each row
        begin
            set new.`fullText` = fulltextify(concat_ws(' ',
                new.note_value, cast(new.bullet_index as char)
            ));
        end
        """,
    ),

    # ------------------------------------------------------- dbo_frnCrossReference
    (
        "trg_dbo_frnCrossReference_bi",
        """
        create trigger trg_dbo_frnCrossReference_bi
        before insert on dbo_frnCrossReference for each row
        begin
            set new.`fullText` = fulltextify(concat_ws(' ',
                new.ref_frn, new.description
            ));
        end
        """,
    ),
    (
        "trg_dbo_frnCrossReference_bu",
        """
        create trigger trg_dbo_frnCrossReference_bu
        before update on dbo_frnCrossReference for each row
        begin
            set new.`fullText` = fulltextify(concat_ws(' ',
                new.ref_frn, new.description
            ));
        end
        """,
    ),
]


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
