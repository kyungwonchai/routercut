"""Flask app: routercut log collector and viewer."""

from __future__ import annotations

import atexit
import io
import mimetypes
import os
import re
import sqlite3
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template, request, send_file

from database import connect, init_db
from pivot import build_pivot_rows, filter_ng_only
from scanner import scan_host
from smb_mount import (
    ensure_mounted,
    force_umount_host,
    mount_point,
    release_mounted,
    umount_all,
)

APP_DIR = Path(__file__).resolve().parent

app = Flask(__name__)
atexit.register(umount_all)
app.config["JSON_AS_ASCII"] = False

_db: sqlite3.Connection | None = None


def get_db() -> sqlite3.Connection:
    global _db
    if _db is None:
        _db = connect()
        init_db(_db)
    return _db


def _host_row(conn: sqlite3.Connection, host_id: int) -> sqlite3.Row | None:
    cur = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,))
    return cur.fetchone()


def _image_abspath(host: sqlite3.Row, folder_date: str, filename: str) -> Path | None:
    if not filename or not re.fullmatch(r"\d{8}", folder_date or ""):
        return None
    fn = os.path.basename(str(filename).replace("\\", "/"))
    if not fn or fn in (".", ".."):
        return None
    root = Path(host["local_root"]).expanduser().resolve()
    sub = host["result_subdir"] or "Result2"
    base = (root / sub).resolve()
    full = (base / folder_date / "Image" / fn).resolve()
    try:
        if hasattr(full, "is_relative_to"):
            if not full.is_relative_to(base):
                return None
        else:
            base_s, full_s = str(base), str(full)
            if os.path.commonpath([base_s, full_s]) != base_s:
                return None
    except (OSError, ValueError):
        return None
    if full.is_file():
        return full
    return None


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/hosts")
def api_hosts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM hosts ORDER BY id").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/hosts")
def api_hosts_create():
    data = request.get_json(force=True, silent=True) or {}
    ip = (data.get("ip") or "").strip()
    name = (data.get("name") or "").strip() or ip
    smb_share = (data.get("smb_share") or "").strip()
    local_root = (data.get("local_root") or "").strip()
    result_subdir = (data.get("result_subdir") or "Result2").strip() or "Result2"
    if not ip:
        return jsonify({"ok": False, "error": "ip is required"}), 400
    if smb_share:
        pending_root = "."
    elif local_root:
        pending_root = local_root
    else:
        return jsonify(
            {"ok": False, "error": "smb_share (SMB 공유 이름) 또는 local_root 가 필요합니다"}
        ), 400
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO hosts (ip, name, local_root, result_subdir, smb_share) VALUES (?, ?, ?, ?, ?)",
            (ip, name, pending_root, result_subdir, smb_share or None),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "duplicate ip"}), 409
    hid = cur.lastrowid
    if smb_share:
        conn.execute(
            "UPDATE hosts SET local_root = ? WHERE id = ?",
            (str(mount_point(hid)), hid),
        )
        conn.commit()
    row = _host_row(conn, hid)
    return jsonify({"ok": True, "host": dict(row)})


@app.patch("/api/hosts/<int:host_id>")
def api_hosts_patch(host_id: int):
    data = request.get_json(force=True, silent=True) or {}
    fields: list[str] = []
    vals: list = []
    for key in ("ip", "name", "result_subdir"):
        if key in data:
            fields.append(f"{key} = ?")
            if key == "result_subdir":
                vals.append((data[key] or "Result2").strip() or "Result2")
            elif key == "ip":
                vals.append((data[key] or "").strip())
            else:
                vals.append(data[key])

    if "smb_share" in data or "local_root" in data:
        smb_v = None
        if "smb_share" in data:
            smb_v = (data["smb_share"] or "").strip() or None
        lr_v = (data["local_root"] or "").strip() if "local_root" in data else None

        if smb_v:
            fields.extend(["smb_share = ?", "local_root = ?"])
            vals.extend([smb_v, str(mount_point(host_id))])
        elif "smb_share" in data and not smb_v and lr_v:
            fields.extend(["smb_share = ?", "local_root = ?"])
            vals.extend([None, lr_v])
        elif "local_root" in data and "smb_share" not in data:
            fields.append("local_root = ?")
            vals.append(lr_v)
        elif "smb_share" in data and not smb_v and not lr_v:
            return jsonify(
                {"ok": False, "error": "SMB 해제 시 local_root(절대 경로)가 필요합니다"}
            ), 400

    if not fields:
        return jsonify({"ok": False, "error": "no fields"}), 400
    vals.append(host_id)
    conn = get_db()
    row0 = _host_row(conn, host_id)
    if not row0:
        return jsonify({"ok": False, "error": "not found"}), 404
    try:
        conn.execute(f"UPDATE hosts SET {', '.join(fields)} WHERE id = ?", vals)
        conn.commit()
    except sqlite3.IntegrityError:
        return jsonify({"ok": False, "error": "duplicate ip"}), 409
    row = _host_row(conn, host_id)
    return jsonify({"ok": True, "host": dict(row)})


@app.delete("/api/hosts/<int:host_id>")
def api_hosts_delete(host_id: int):
    force_umount_host(host_id)
    conn = get_db()
    conn.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
    conn.commit()
    return jsonify({"ok": True})


@app.post("/api/hosts/<int:host_id>/scan")
def api_scan(host_id: int):
    conn = get_db()
    res = scan_host(conn, host_id)
    code = 200 if res["ok"] else 400
    return jsonify(res), code


@app.post("/api/scan_all")
def api_scan_all():
    conn = get_db()
    ids = [r["id"] for r in conn.execute("SELECT id FROM hosts").fetchall()]
    results = []
    for hid in ids:
        results.append({"host_id": hid, **scan_host(conn, hid)})
    return jsonify({"ok": True, "results": results})


@app.get("/api/dates")
def api_dates():
    host_id = request.args.get("host_id", type=int)
    conn = get_db()
    if host_id:
        rows = conn.execute(
            "SELECT DISTINCT folder_date FROM records WHERE host_id = ? ORDER BY folder_date DESC",
            (host_id,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT DISTINCT folder_date FROM records ORDER BY folder_date DESC"
        ).fetchall()
    return jsonify([r[0] for r in rows])


def _fetch_flat_records(
    conn: sqlite3.Connection,
    host_id: int | None,
    folder_date: str | None,
    q: str | None,
) -> list[dict]:
    sql = """
        SELECT r.*, h.ip AS host_ip
        FROM records r
        JOIN hosts h ON h.id = r.host_id
        WHERE 1=1
    """
    params: list = []
    if host_id:
        sql += " AND r.host_id = ?"
        params.append(host_id)
    if folder_date:
        sql += " AND r.folder_date = ?"
        params.append(folder_date)
    if q:
        like = f"%{q}%"
        sql += " AND (r.barcode LIKE ? OR r.model LIKE ? OR r.time LIKE ?)"
        params.extend([like, like, like])
    sql += " ORDER BY r.folder_date ASC, r.time ASC, r.barcode ASC, r.cam ASC, r.roi ASC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/records")
def api_records():
    host_id = request.args.get("host_id", type=int)
    folder_date = (request.args.get("date") or "").strip() or None
    q = (request.args.get("q") or "").strip() or None
    ng_only = request.args.get("ng_only", "0") in ("1", "true", "yes")
    pivot = request.args.get("pivot", "1") not in ("0", "false", "no")

    conn = get_db()
    flat = _fetch_flat_records(conn, host_id, folder_date, q)
    if not pivot:
        return jsonify({"columns": list(flat[0].keys()) if flat else [], "rows": flat})
    cols, pivoted = build_pivot_rows(flat)
    if ng_only:
        pivoted = filter_ng_only(pivoted)
    return jsonify({"columns": cols, "rows": pivoted})


@app.get("/api/image")
def api_image():
    host_id = request.args.get("host_id", type=int)
    folder_date = (request.args.get("folder_date") or "").strip()
    file = (request.args.get("file") or "").strip()
    if not host_id or not folder_date or not file:
        return "bad request", 400
    conn = get_db()
    host = _host_row(conn, host_id)
    if not host:
        return "not found", 404
    use_smb = bool((host["smb_share"] or "").strip())
    if use_smb:
        ok_m, err_m = ensure_mounted(host)
        if not ok_m:
            return err_m, 500
    try:
        path = _image_abspath(host, folder_date, file)
        if not path:
            return "not found", 404
        if use_smb:
            blob = path.read_bytes()
            mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return send_file(io.BytesIO(blob), mimetype=mime, as_attachment=False)
        return send_file(path, as_attachment=False)
    finally:
        if use_smb:
            release_mounted(host_id)


@app.get("/api/export.xlsx")
def api_export():
    host_id = request.args.get("host_id", type=int)
    folder_date = (request.args.get("date") or "").strip() or None
    q = (request.args.get("q") or "").strip() or None
    ng_only = request.args.get("ng_only", "0") in ("1", "true", "yes")

    conn = get_db()
    flat = _fetch_flat_records(conn, host_id, folder_date, q)
    _, pivoted = build_pivot_rows(flat)
    if ng_only:
        pivoted = filter_ng_only(pivoted)
    for r in pivoted:
        r.pop("has_ng", None)
    df = pd.DataFrame(pivoted)
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="routercut")
    buf.seek(0)
    fname = f"routercut_{folder_date or 'all'}_{host_id or 'all'}.xlsx"
    return send_file(
        buf,
        as_attachment=True,
        download_name=fname,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/favicon.ico")
def favicon():
    return ("", 204)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=True)
