"""Pivot raw records into wide rows for UI / Excel."""

from __future__ import annotations


def build_pivot_rows(flat_rows: list[dict]) -> tuple[list[str], list[dict]]:
    """
    flat_rows: list of dicts with keys model, time, barcode, cam, roi, result, value, spec,
               image_file, host_ip, folder_date, id (roi row id optional)
    Returns (column_names, pivoted_rows).
    """
    groups: dict[tuple, dict] = {}
    roi_set: set[int] = set()

    for r in flat_rows:
        key = (r["time"], r["barcode"], r["cam"], r.get("model", ""))
        roi = int(r["roi"])
        roi_set.add(roi)
        if key not in groups:
            groups[key] = {
                "model": r.get("model", ""),
                "time": r["time"],
                "barcode": r["barcode"],
                "cam": r["cam"],
                "spec": r.get("spec", ""),
                "image_file": r.get("image_file"),
                "folder_date": r.get("folder_date"),
                "host_ip": r.get("host_ip"),
                "host_id": r.get("host_id"),
                "rois": {},
            }
        g = groups[key]
        if not g.get("spec") and r.get("spec"):
            g["spec"] = r["spec"]
        g["rois"][roi] = {"result": r["result"], "value": r.get("value", "")}
        if r.get("image_file"):
            g["image_file"] = r["image_file"]

    sorted_rois = sorted(roi_set)
    columns = (
        ["host_id", "host_ip", "folder_date", "model", "time", "barcode", "cam", "spec"]
        + [x for roi in sorted_rois for x in (f"ROI{roi}_VALUE", f"ROI{roi}_RESULT")]
        + ["image_file", "has_ng"]
    )

    out: list[dict] = []
    for key in sorted(groups.keys(), key=lambda k: (k[0], k[1], k[2])):
        g = groups[key]
        row = {
            "host_id": g["host_id"],
            "host_ip": g["host_ip"],
            "folder_date": g["folder_date"],
            "model": g["model"],
            "time": g["time"],
            "barcode": g["barcode"],
            "cam": g["cam"],
            "spec": g["spec"],
            "image_file": g.get("image_file"),
        }
        for roi in sorted_rois:
            cell = g["rois"].get(roi)
            if cell:
                row[f"ROI{roi}_VALUE"] = cell["value"]
                row[f"ROI{roi}_RESULT"] = cell["result"]
            else:
                row[f"ROI{roi}_VALUE"] = ""
                row[f"ROI{roi}_RESULT"] = ""
        row["has_ng"] = any(
            g["rois"][r]["result"] == "NG" for r in g["rois"]
        )
        out.append(row)

    return columns, out


def filter_ng_only(pivoted: list[dict]) -> list[dict]:
    return [r for r in pivoted if r.get("has_ng")]
