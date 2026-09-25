import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path


LOG_PATH = "/home/alex/homelab_project/homelab/.cursor/debug-465b5a.log"
SESSION_ID = "465b5a"


def ndjson_log(*, run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    payload = {
        "sessionId": SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def find_db_files(config_dir: Path) -> list[Path]:
    # Prowlarr uses a single SQLite DB under /config; the exact name can vary by version.
    candidates = sorted(config_dir.glob("**/*.db"))
    return candidates


def find_sqlite_candidates_fast(config_dir: Path) -> list[Path]:
    """
    Fast candidate collection (avoid deep recursion on large NFS copies).
    Checks common SQLite locations and filename patterns without walking everything.
    """
    scan_dirs = [
        config_dir,
        config_dir / "db",
        config_dir / "data",
        config_dir / "database",
        config_dir / "logs",
    ]

    sqlite_exts = {".db", ".sqlite", ".sqlite3", ".sqlite4"}
    name_frags = ("db", "sqlite", "logs")  # covers likely Prowlarr naming too

    candidates: list[Path] = []
    for d in scan_dirs:
        if not d.exists() or not d.is_dir():
            continue
        for p in d.iterdir():
            if not p.is_file():
                continue
            name_l = p.name.lower()
            ext_l = p.suffix.lower()
            if (ext_l in sqlite_exts) or any(frag in name_l for frag in name_frags):
                candidates.append(p)

    # De-dup while keeping stable order.
    seen: set[str] = set()
    out: list[Path] = []
    for p in sorted(candidates):
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def scan_for_sqlite_magic(
    config_dir: Path,
    *,
    max_files: int = 5000,
    max_results: int = 10,
    max_depth: int = 6,
    filtered_by_name: bool = True,
) -> dict:
    """
    Fallback when the DB filename/extension doesn't match '*.db'.
    We scan for files whose first 16 bytes match SQLite's magic header.
    """
    sqlite_magic = b"SQLite format 3\x00"
    scanned = 0
    hits: list[dict] = []

    for p in config_dir.rglob("*"):
        if len(hits) >= max_results:
            break
        if not p.is_file():
            continue
        # Avoid crawling deeply nested config/app data.
        try:
            rel_parts = p.relative_to(config_dir).parts
            if len(rel_parts) > max_depth:
                continue
        except Exception:
            continue

        if filtered_by_name:
            name_l = p.name.lower()
            ext_l = p.suffix.lower()
            common_sqlite_exts = {".db", ".sqlite", ".sqlite3", ".sqlite4"}
            looks_sqlite = ("db" in name_l) or ("sqlite" in name_l) or (ext_l in common_sqlite_exts)
            if not looks_sqlite:
                continue

        scanned += 1
        if scanned > max_files:
            break
        try:
            with open(p, "rb") as f:
                b = f.read(16)
            if b == sqlite_magic:
                size = p.stat().st_size
                hits.append({"path": str(p), "size_bytes": size})
        except Exception:
            # Ignore unreadable files; evidence will still be in the logs.
            continue

    return {"scanned_files": scanned, "sqlite_magic_hits": hits, "hits_count": len(hits)}


def read_header_16(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            b = f.read(16)
        return {"header_len": len(b), "header_hex": b.hex(), "sqlite_magic_match": b == b"SQLite format 3\x00"}
    except Exception as e:
        return {"error_type": type(e).__name__, "error_message": str(e)}


def safe_sqlite_connect_integrity(db_path: Path) -> dict:
    # Read-only connection string so we don't modify or auto-journal the DB file.
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = None
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        cur = conn.cursor()
        cur.execute("PRAGMA schema_version;")
        schema_version = cur.fetchone()[0]
        cur.execute("PRAGMA journal_mode;")
        journal_mode = cur.fetchone()[0]
        cur.execute("PRAGMA integrity_check;")
        row = cur.fetchone()
        integrity_check = row[0] if row else None
        # Light schema evidence: does the expected 'Users' table exist?
        # (Prowlarr uses Dapper to query user auth data.)
        user_tables: list[str] = []
        try:
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND lower(name) LIKE '%user%' LIMIT 50;"
            )
            user_tables = [r[0] for r in (cur.fetchall() or [])]
        except Exception as e:
            user_tables = []
            _ = str(e)

        try:
            cur.execute("SELECT COUNT(*) FROM sqlite_master;")
            sqlite_master_row_count = cur.fetchone()[0]
        except Exception:
            sqlite_master_row_count = None

        return {
            "connect_ok": True,
            "schema_version": schema_version,
            "journal_mode": journal_mode,
            "integrity_check": integrity_check,
            "user_table_names": user_tables,
            "sqlite_master_row_count": sqlite_master_row_count,
        }
    except Exception as e:
        return {"connect_ok": False, "error_type": type(e).__name__, "error_message": str(e)}
    finally:
        if conn is not None:
            conn.close()


def find_sqlite_candidates(config_dir: Path) -> list[Path]:
    """
    Collect likely SQLite files from a config directory.
    Prefer '*.db', but also include any file whose first bytes match SQLite magic.
    """
    # Default to fast to keep evidence runs practical.
    return find_sqlite_candidates_fast(config_dir)


def inspect_sqlite_candidates(candidates: list[Path]) -> dict:
    results: list[dict] = []
    for p in candidates[:10]:
        wal_path = Path(str(p) + "-wal")
        shm_path = Path(str(p) + "-shm")
        header = read_header_16(p)
        integrity = safe_sqlite_connect_integrity(p)
        wal_size = wal_path.stat().st_size if wal_path.exists() else None
        shm_size = shm_path.stat().st_size if shm_path.exists() else None
        results.append(
            {
                "path": str(p),
                "size_bytes": p.stat().st_size if p.exists() else None,
                "wal_exists": wal_path.exists(),
                "wal_size_bytes": wal_size,
                "shm_exists": shm_path.exists(),
                "shm_size_bytes": shm_size,
                "sqlite_magic_match": header.get("sqlite_magic_match"),
                "connect_ok": integrity.get("connect_ok"),
                "integrity_check": integrity.get("integrity_check"),
                "error_type": integrity.get("error_type"),
            }
        )

    return {"candidate_count": len(candidates), "inspected_count": len(results), "results": results}


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect Prowlarr SQLite DB for corruption evidence.")
    parser.add_argument("--config-dir", type=str, default="", help="Local directory containing Prowlarr /config contents.")
    parser.add_argument("--db-path", type=str, default="", help="Direct path to the SQLite DB file to test.")
    parser.add_argument("--run-id", type=str, default="pre-debug", help="Label to distinguish debug runs.")
    parser.add_argument(
        "--scan-mode",
        type=str,
        default="fast",
        choices=["fast", "deep"],
        help="fast avoids deep recursive scans; deep probes more extensively.",
    )
    args = parser.parse_args()

    config_dir = Path(args.config_dir) if args.config_dir else None
    db_path = Path(args.db_path) if args.db_path else None

    if not db_path and not config_dir:
        print("Provide --config-dir or --db-path")
        return 2

    if not db_path:
        dbs = find_db_files(config_dir)  # type: ignore[arg-type]
        # H5: DB filename/extension doesn't match *.db (e.g. *.sqlite or extensionless).
        if not dbs:
            if args.scan_mode == "fast":
                scan = scan_for_sqlite_magic(config_dir, max_files=500, max_depth=2)  # type: ignore[arg-type]
            else:
                scan = scan_for_sqlite_magic(config_dir)  # type: ignore[arg-type]
            #region agent log (H5 sqlite magic scan)
            ndjson_log(
                run_id=args.run_id,
                hypothesis_id="H5_sqlite_magic_scan_fallback",
                location="scripts/debug_prowlarr_db.py:scan_for_sqlite_magic",
                message="Fallback scan for SQLite header bytes",
                data={"config_dir": str(config_dir), **scan},
            )
            #endregion agent log (H5 sqlite magic scan)

            hits = scan.get("sqlite_magic_hits") or []
            if not hits:
                print(f"No SQLite DB magic header found under {config_dir}")
                return 4
            db_path = Path(hits[0]["path"])
        else:
            db_path = dbs[0]

    db_path = db_path.resolve()
    config_root = config_dir.resolve() if config_dir else db_path.parent

    # H6: The reported SQLite corruption might be in a *different* DB file
    # than the first one we heuristically picked.
    if config_dir is not None:
        if args.scan_mode == "fast":
            candidates = find_sqlite_candidates_fast(config_dir)
        else:
            # Deep mode: avoid expensive '**/*.db' recursion; use magic scan + fast candidates.
            candidates = set(find_sqlite_candidates_fast(config_dir))
            scan = scan_for_sqlite_magic(
                config_dir,
                max_results=10,
                filtered_by_name=True,
                max_depth=8,
                max_files=20000,
            )
            for hit in scan.get("sqlite_magic_hits") or []:
                candidates.add(Path(hit["path"]))
            candidates = sorted(candidates)
        #region agent log (H6 candidates scan)
        ndjson_log(
            run_id=args.run_id,
            hypothesis_id="H6_multi_sqlite_candidates_in_config",
            location="scripts/debug_prowlarr_db.py:inspect_sqlite_candidates",
            message="Scan + integrity check across SQLite candidates",
            data={
                "config_dir": str(config_dir),
                **inspect_sqlite_candidates(candidates),
            },
        )
        #endregion agent log (H6 candidates scan)

    wal_path = Path(str(db_path) + "-wal")
    shm_path = Path(str(db_path) + "-shm")

    print(f"Using DB: {db_path}")
    print(f"Config root: {config_root}")
    print(f"WAL exists: {wal_path.exists()} SHM exists: {shm_path.exists()}")

    # H1: The SQLite header/magic bytes are invalid (true corruption at byte level).
    header = read_header_16(db_path)
    #region agent log (H1 header)
    ndjson_log(
        run_id=args.run_id,
        hypothesis_id="H1_header_corrupt",
        location="scripts/debug_prowlarr_db.py:read_header_16",
        message="SQLite header check result",
        data={
            "db_path": str(db_path),
            "wal_path": str(wal_path) if wal_path.exists() else None,
            "shm_path": str(shm_path) if shm_path.exists() else None,
            **header,
        },
    )
    #endregion agent log (H1 header)

    # H2: SQLite cannot connect/read due to "disk image malformed".
    integrity = safe_sqlite_connect_integrity(db_path)
    #region agent log (H2 connect/integrity)
    ndjson_log(
        run_id=args.run_id,
        hypothesis_id="H2_connect_or_integrity_fail",
        location="scripts/debug_prowlarr_db.py:safe_sqlite_connect_integrity",
        message="Read-only connect + integrity_check outcome",
        data={"db_path": str(db_path), **integrity},
    )
    #endregion agent log (H2 connect/integrity)

    # H3: WAL/shm artifacts exist, suggesting an interrupted WAL recovery scenario.
    # We don't assert this is causal; we just record the presence as evidence.
    #region agent log (H3 WAL present)
    ndjson_log(
        run_id=args.run_id,
        hypothesis_id="H3_wal_artifacts_present",
        location="scripts/debug_prowlarr_db.py:wal_presence",
        message="WAL/shm artifact presence",
        data={
            "db_path": str(db_path),
            "wal_exists": wal_path.exists(),
            "wal_size_bytes": wal_path.stat().st_size if wal_path.exists() else None,
            "shm_exists": shm_path.exists(),
            "shm_size_bytes": shm_path.stat().st_size if shm_path.exists() else None,
        },
    )
    #endregion agent log (H3 WAL present)

    # H4: The DB file is present but unusually small (often correlates with incomplete writes).
    # This is a weak indicator but useful as additional evidence.
    #region agent log (H4 file size)
    size_bytes = db_path.stat().st_size if db_path.exists() else None
    ndjson_log(
        run_id=args.run_id,
        hypothesis_id="H4_file_size_suspicious",
        location="scripts/debug_prowlarr_db.py:file_size",
        message="DB file size evidence",
        data={"db_path": str(db_path), "size_bytes": size_bytes},
    )
    #endregion agent log (H4 file size)

    print("Wrote evidence to:")
    print(LOG_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

