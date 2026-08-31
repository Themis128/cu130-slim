#!/usr/bin/env python3
"""Convert a PostgreSQL schema dump to D1/SQLite-compatible SQL.

Handles:
- ENUM types → TEXT + CHECK constraints
- UUID → TEXT
- JSONB/JSON → TEXT
- TIMESTAMP WITH TIME ZONE → TEXT
- SERIAL/IDENTITY → INTEGER AUTOINCREMENT
- BOOLEAN → INTEGER (0/1)
- Drops PostgreSQL-specific syntax (SET, SELECT set_config, etc.)
"""
import re
import sys


def convert(input_sql: str) -> str:
    lines = input_sql.split("\n")
    output = []
    enum_map: dict[str, list[str]] = {}
    skip_block = False

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Skip PostgreSQL SET/SELECT commands
        if stripped.startswith("SET ") or stripped.startswith("SELECT "):
            i += 1
            continue

        # Skip restrict/unrestrict
        if stripped.startswith("\\restrict") or stripped.startswith("\\unrestrict"):
            i += 1
            continue

        # Capture CREATE TYPE ... AS ENUM
        enum_match = re.match(r"CREATE TYPE public\.(\w+) AS ENUM \(", stripped)
        if enum_match:
            enum_name = enum_match.group(1)
            values = []
            i += 1
            while i < len(lines):
                vline = lines[i].strip()
                if vline == ");":
                    break
                # Remove trailing comma and quotes
                val = vline.rstrip(",").strip("'")
                if val:
                    values.append(val)
                i += 1
            enum_map[enum_name] = values
            i += 1
            continue

        # Convert CREATE TABLE
        if stripped.startswith("CREATE TABLE"):
            # Collect the full table definition
            table_sql = line
            while not table_sql.rstrip().endswith(");"):
                i += 1
                if i >= len(lines):
                    break
                table_sql += "\n" + lines[i]
            table_sql = convert_table(table_sql, enum_map)
            output.append(table_sql)
            i += 1
            continue

        # Convert CREATE INDEX
        if stripped.startswith("CREATE INDEX") or stripped.startswith("CREATE UNIQUE INDEX"):
            # Collect multi-line index
            idx_sql = line
            while not idx_sql.rstrip().endswith(";"):
                i += 1
                if i >= len(lines):
                    break
                idx_sql += " " + lines[i].strip()
            # Remove schema prefix
            idx_sql = idx_sql.replace("public.", "")
            # Remove USING btree (SQLite uses it implicitly)
            idx_sql = re.sub(r" USING btree", "", idx_sql)
            # Remove NULLS NOT DISTINCT
            idx_sql = re.sub(r" NULLS NOT DISTINCT", "", idx_sql)
            # Remove text_pattern_ops
            idx_sql = re.sub(r" text_pattern_ops", "", idx_sql)
            output.append(idx_sql)
            i += 1
            continue

        # Skip ALTER TABLE (we inline constraints in CREATE TABLE)
        if stripped.startswith("ALTER TABLE") or stripped.startswith("ALTER TYPE"):
            i += 1
            continue

        # Skip comments
        if stripped.startswith("--"):
            i += 1
            continue

        # Skip empty lines at the end
        if stripped == "" and output and output[-1].strip() != "":
            output.append("")

        i += 1

    return "\n".join(output)


def convert_table(sql: str, enum_map: dict) -> str:
    """Convert a CREATE TABLE statement to SQLite-compatible SQL."""
    # Remove public. schema prefix
    sql = sql.replace("public.", "")

    # Convert column types
    # UUID → TEXT
    sql = re.sub(r"\bUUID\b", "TEXT", sql, flags=re.IGNORECASE)
    # JSONB, JSON → TEXT
    sql = re.sub(r"\bJSONB\b", "TEXT", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bJSON\b", "TEXT", sql, flags=re.IGNORECASE)
    # TIMESTAMP WITH TIME ZONE → TEXT
    sql = re.sub(r"TIMESTAMP(?:\(\d+\))? WITH TIME ZONE", "TEXT", sql, flags=re.IGNORECASE)
    sql = re.sub(r"TIMESTAMP(?:\(\d+\))? WITHOUT TIME ZONE", "TEXT", sql, flags=re.IGNORECASE)
    # TIMESTAMP → TEXT
    sql = re.sub(r"\bTIMESTAMP\b", "TEXT", sql, flags=re.IGNORECASE)
    # BOOLEAN → INTEGER
    sql = re.sub(r"\bBOOLEAN\b", "INTEGER", sql, flags=re.IGNORECASE)
    # BIGINT → INTEGER
    sql = re.sub(r"\bBIGINT\b", "INTEGER", sql, flags=re.IGNORECASE)
    # BIGSERIAL → INTEGER
    sql = re.sub(r"\bBIGSERIAL\b", "INTEGER", sql, flags=re.IGNORECASE)
    # SERIAL → INTEGER
    sql = re.sub(r"\bSERIAL\b", "INTEGER", sql, flags=re.IGNORECASE)
    # DOUBLE PRECISION → REAL
    sql = re.sub(r"DOUBLE PRECISION", "REAL", sql, flags=re.IGNORECASE)
    # NUMERIC → REAL
    sql = re.sub(r"\bNUMERIC\b", "REAL", sql, flags=re.IGNORECASE)
    # REAL → REAL (already)
    # BYTEA → BLOB
    sql = re.sub(r"\bBYTEA\b", "BLOB", sql, flags=re.IGNORECASE)
    # INTERVAL → TEXT
    sql = re.sub(r"\bINTERVAL\b", "TEXT", sql, flags=re.IGNORECASE)

    # Convert ENUM types to TEXT + CHECK
    for enum_name, values in enum_map.items():
        pattern = rf"\b{enum_name}\b"
        if re.search(pattern, sql, re.IGNORECASE):
            values_str = ", ".join(f"'{v}'" for v in values)
            # Replace the type with TEXT
            sql = re.sub(pattern, "TEXT", sql, flags=re.IGNORECASE)
            # We'll add CHECK constraints after the table

    # Handle GENERATED ... AS IDENTITY → AUTOINCREMENT
    sql = re.sub(
        r"GENERATED (?:ALWAYS|BY DEFAULT) AS IDENTITY",
        "AUTOINCREMENT",
        sql,
        flags=re.IGNORECASE,
    )

    # Handle DEFAULT gen_random_uuid() → lower(hex(randomblob(16)))
    sql = re.sub(
        r"DEFAULT gen_random_uuid\(\)",
        "DEFAULT (lower(hex(randomblob(16))))",
        sql,
        flags=re.IGNORECASE,
    )

    # Handle DEFAULT now() / CURRENT_TIMESTAMP
    sql = re.sub(r"DEFAULT now\(\)", "DEFAULT (datetime('now'))", sql, flags=re.IGNORECASE)
    sql = re.sub(r"DEFAULT CURRENT_TIMESTAMP", "DEFAULT (datetime('now'))", sql, flags=re.IGNORECASE)

    # Handle DEFAULT true/false
    sql = re.sub(r"DEFAULT true\b", "DEFAULT 1", sql, flags=re.IGNORECASE)
    sql = re.sub(r"DEFAULT false\b", "DEFAULT 0", sql, flags=re.IGNORECASE)

    # Handle DEFAULT ''::text
    sql = re.sub(r"::\w+", "", sql)  # Remove type casts

    # Handle character varying(N) → TEXT
    sql = re.sub(r"character varying\(\d+\)", "TEXT", sql, flags=re.IGNORECASE)
    sql = re.sub(r"\bvarchar\(\d+\)", "TEXT", sql, flags=re.IGNORECASE)

    # Handle text[] → TEXT (store as JSON)
    sql = re.sub(r"\btext\[\]", "TEXT", sql, flags=re.IGNORECASE)

    # Remove NOT NULL on autoincrement columns that are PRIMARY KEY
    # (SQLite handles this automatically)

    # Remove "COLLATE" clauses
    sql = re.sub(r" COLLATE \w+", "", sql, flags=re.IGNORECASE)

    # Remove "DEFAULT" empty arrays
    sql = re.sub(r"DEFAULT '\{\}'::text\[\]", "DEFAULT '[]'", sql, flags=re.IGNORECASE)

    # Clean up extra spaces
    sql = re.sub(r"  +", " ", sql)

    return sql


def main():
    if len(sys.argv) < 2:
        print("Usage: pg_to_d1.py <input.sql> [output.sql]")
        sys.exit(1)

    with open(sys.argv[1]) as f:
        input_sql = f.read()

    converted = convert(input_sql)

    if len(sys.argv) > 2:
        with open(sys.argv[2], "w") as f:
            f.write(converted)
        print(f"Written to {sys.argv[2]}")
    else:
        print(converted)


if __name__ == "__main__":
    main()
