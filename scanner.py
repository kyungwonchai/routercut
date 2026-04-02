"""Scan <yyyyMMdd>/Result.csv under host root (SMB share root or local dir)."""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path

import pandas as pd

from database import get_smb_credentials
from smb_mount import ensure_mounted, release_mounted

RE_DATE_DIR = re.compile(r"^\d{8}$")
RE_PATH_IMAGE = re.compile(r"[/\\]Image[/\\]([^/\\]+)$", re.I)
# After 'Result' (case-insensitive), optional non-digits, then 8-digit date and rest
RE_AFTER_RESULT = re.compile(r"Result\D*?(\d{8})([/\\].+)$", re.I)


def _result_base_path(host_row: sqlite3.Row) -> Path:
    return Path(host_row["local_root"]).expanduser().resolve()


def _normalize_columns(df: pd.DataFrame) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    df = df.copy()
    df.columns = [str(c).strip().lstrip("\ufeff").upper() for c in df.columns]
    required = {"MODEL", "TIME", "BARCODE", "CAM", "ROI", "RESULT", "VALUE", "SPEC", "PATH"}
    if not required.issubset(set(df.columns)):
        return None
    return df


def _parse_image_basename(path_val: str, folder_date: str, image_dir: Path) -> str | None:
    if path_val is None or (isinstance(path_val, float) and pd.isna(path_val)):
        return None
    s = str(path_val).strip()
    if not s:
        return None
    s = s.replace("￦", "\\").replace("\uFF3C", "\\")
    m = RE_PATH_IMAGE.search(s)
    if m:
        fn = m.group(1)
    else:
        m2 = RE_AFTER_RESULT.search(s)
        if m2 and m2.group(1) == folder_date:
            rest = m2.group(2).replace("\\", "/")
            if "/Image/" in rest.lower():
                fn = rest.rsplit("/", 1)[-1]
            else:
                fn = os.path.basename(rest)
        else:
            fn = os.path.basename(s)
    if not fn:
        return None
    cand = image_dir / fn
    if cand.is_file():
        return fn
    return fn


def _scan_host_run(conn: sqlite3.Connection, host_id: int, host: sqlite3.Row) -> dict:
    base = _result_base_path(host)
    if not base.is_dir():
        return {"ok": False, "error": f"local path not found: {base}", "rows": 0, "folders": []}

    total_upsert = 0
    touched_folders: list[str] = []

    try:
        entries = sorted(os.listdir(base))
    except OSError as e:
        return {"ok": False, "error": str(e), "rows": 0, "folders": []}

    for name in entries:
        if not RE_DATE_DIR.match(name):
            continue
        csv_path = base / name / "Result.csv"
        if not csv_path.is_file():
            continue

        image_dir = base / name / "Image"

        try:
            df = pd.read_csv(csv_path, encoding="utf-8-sig", on_bad_lines="skip")
        except UnicodeDecodeError:
            df = pd.read_csv(csv_path, encoding="cp949", on_bad_lines="skip")
        except Exception as e:
            return {"ok": False, "error": f"{csv_path}: {e}", "rows": total_upsert, "folders": touched_folders}

        df = _normalize_columns(df)
        if df is None:
            continue

        conn.execute(
            "DELETE FROM records WHERE host_id = ? AND folder_date = ?",
            (host_id, name),
        )

        rows_batch: list[tuple] = []
        for _, row in df.iterrows():
            try:
                model = str(row["MODEL"])[:50] if pd.notna(row["MODEL"]) else ""
                time_s = str(row["TIME"]).strip()[:32]
                barcode = re.sub(r"\D", "", str(row["BARCODE"]))[:20]
                cam = int(float(row["CAM"]))
                roi = int(float(row["ROI"]))
                result = str(row["RESULT"]).strip().upper()
                if result not in ("OK", "NG"):
                    if "NG" in result:
                        result = "NG"
                    else:
                        result = "OK"
                value = "" if pd.isna(row["VALUE"]) else str(row["VALUE"]).strip()
                spec = "" if pd.isna(row["SPEC"]) else str(row["SPEC"]).strip()
                path_raw = row["PATH"]
                img_fn = _parse_image_basename(path_raw, name, image_dir)
            except (TypeError, ValueError, KeyError):
                continue

            rows_batch.append(
                (
                    host_id,
                    name,
                    model,
                    time_s,
                    barcode,
                    cam,
                    roi,
                    result,
                    value,
                    spec,
                    img_fn,
                    str(csv_path),
                )
            )

        conn.executemany(
            """
            INSERT INTO records (
                host_id, folder_date, model, time, barcode, cam, roi,
                result, value, spec, image_file, source_csv
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(host_id, folder_date, time, barcode, cam, roi) DO UPDATE SET
                model = excluded.model,
                result = excluded.result,
                value = excluded.value,
                spec = excluded.spec,
                image_file = COALESCE(excluded.image_file, records.image_file),
                source_csv = excluded.source_csv
            """,
            rows_batch,
        )
        total_upsert += len(rows_batch)
        touched_folders.append(name)

    conn.commit()
    return {"ok": True, "error": None, "rows": total_upsert, "folders": touched_folders}


def scan_host(conn: sqlite3.Connection, host_id: int) -> dict:
    cur = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,))
    host = cur.fetchone()
    if not host:
        return {"ok": False, "error": "host not found", "rows": 0, "folders": []}

    u, p, d = get_smb_credentials(conn)
    ok_m, err_m = ensure_mounted(host, cred_user=u, cred_password=p, cred_domain=d)
    if not ok_m:
        return {"ok": False, "error": err_m, "rows": 0, "folders": []}
    try:
        return _scan_host_run(conn, host_id, host)
    finally:
        release_mounted(host_id)
