"""Build static leaderboard/index artifacts from validated submissions."""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .validate import SCHEMA_VERSION, ValidatedBundle, discover_bundles, validate_bundle


def _nested_string(data: dict[str, Any], *path: str) -> str | None:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current if isinstance(current, str) and current else None


def _model_ids(result: dict[str, Any]) -> list[str]:
    model_ids = result.get("model_ids")
    if isinstance(model_ids, list):
        return [item for item in model_ids if isinstance(item, str)]

    models = result.get("models")
    if not isinstance(models, list):
        config = result.get("config")
        if isinstance(config, dict):
            models = config.get("models")
    if not isinstance(models, list):
        metadata = result.get("model_metadata")
        if isinstance(metadata, list):
            models = metadata
    if isinstance(models, list):
        extracted: list[str] = []
        for model in models:
            if isinstance(model, dict):
                model_id = model.get("id", model.get("model_id"))
                if isinstance(model_id, str):
                    extracted.append(model_id)
        return extracted
    return []


def _signer(manifest: dict[str, Any]) -> str | None:
    signer = manifest.get("signer")
    if not isinstance(signer, dict):
        return None
    for field in ("identity", "subject", "repository"):
        value = signer.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _model_family(result: dict[str, Any]) -> str:
    provenance = result.get("provenance")
    if isinstance(provenance, dict):
        fam = provenance.get("model_family")
        if isinstance(fam, str) and fam:
            return fam
    models = result.get("models")
    if isinstance(models, list):
        for m in models:
            if isinstance(m, dict):
                fam = m.get("model_family")
                if isinstance(fam, str) and fam:
                    return fam
    return "unknown"


def _architecture(result: dict[str, Any]) -> str:
    provenance = result.get("provenance")
    if isinstance(provenance, dict):
        arch = provenance.get("architecture")
        if isinstance(arch, str) and arch:
            return arch
    return "unknown"


def _params_total(result: dict[str, Any]) -> str:
    provenance = result.get("provenance")
    if isinstance(provenance, dict):
        p = provenance.get("parameters")
        if isinstance(p, str) and p:
            return p
    models = result.get("models")
    if isinstance(models, list):
        for m in models:
            if isinstance(m, dict):
                p = m.get("parameters")
                if isinstance(p, str) and p:
                    return p
    return "unknown"


def _params_active(result: dict[str, Any]) -> str:
    models = result.get("models")
    if isinstance(models, list):
        for m in models:
            if isinstance(m, dict):
                p = m.get("active_parameters") or m.get("active_params")
                if isinstance(p, str) and p:
                    return p
    return None


def _context_length(result: dict[str, Any]) -> str:
    provenance = result.get("provenance")
    if isinstance(provenance, dict):
        cl = provenance.get("context_length")
        if isinstance(cl, str) and cl:
            return cl
        cl = provenance.get("context")
        if isinstance(cl, str) and cl:
            return cl
    models = result.get("models")
    if isinstance(models, list):
        for m in models:
            if isinstance(m, dict):
                cl = m.get("context_length") or m.get("context")
                if isinstance(cl, str) and cl:
                    return cl
    return "unknown"


def _quantization(result: dict[str, Any]) -> str:
    provenance = result.get("provenance")
    if isinstance(provenance, dict):
        q = provenance.get("quantization")
        if isinstance(q, str) and q:
            return q
    models = result.get("models")
    if isinstance(models, list):
        for m in models:
            if isinstance(m, dict):
                q = m.get("quantization")
                if isinstance(q, str) and q:
                    return q
    return "unknown"


def _hardware(result: dict[str, Any]) -> dict[str, Any] | str:
    hw = result.get("hardware")
    if isinstance(hw, dict):
        return hw
    if isinstance(hw, str) and hw.strip():
        return {"device_name": hw}
    return "unknown"


def index_entry(bundle: ValidatedBundle) -> dict[str, Any]:
    result = bundle.result
    return {
        "run_id": result["run_id"],
        "timestamp": result["timestamp"],
        "signer": _signer(bundle.manifest),
        "model_ids": _model_ids(result),
        "model_family": _model_family(result),
        "architecture": _architecture(result),
        "params_total": _params_total(result),
        "params_active": _params_active(result),
        "context_length": _context_length(result),
        "quantization": _quantization(result),
        "judge_mode": result.get("judge_mode")
        or _nested_string(result, "judge", "mode")
        or _nested_string(result, "config", "judge", "mode"),
        "config_hash": result.get("config_hash")
        or _nested_string(result, "provenance", "config_hash")
        or _nested_string(result, "config", "hash")
        or _nested_string(result, "config", "sha256"),
        "hardware": _hardware(result),
        "bundle_path": bundle.path.as_posix(),
    }


def build_index(submissions_dir: Path | str, site_dir: Path | str) -> dict[str, Any]:
    submissions_path = Path(submissions_dir)
    site_path = Path(site_dir)
    bundles = [validate_bundle(path) for path in discover_bundles([submissions_path])]
    entries = sorted(
        (index_entry(bundle) for bundle in bundles),
        key=lambda entry: (entry["timestamp"], entry["run_id"]),
        reverse=True,
    )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "entries": entries,
    }

    site_path.mkdir(parents=True, exist_ok=True)
    (site_path / "index.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (site_path / "index.html").write_text(render_html(payload), encoding="utf-8")
    return payload


def _hw_tier(hw: str | dict) -> str:
    """Classify hardware into tier + memory bucket."""
    if not isinstance(hw, dict):
        return ("unknown", "unknown")
    name = hw.get("device_name", "")
    vram = hw.get("vram_gb")
    pci = hw.get("device_pci", "")
    # GPU detection
    gpu_keywords = (
        "GPU", "RTX", "A100", "H100", "V100", "MI250", "MI300", "A6000",
        "L40", "H200", "B200", "GB100", "MPSA", "MPSB", "MPSL", "B2",
        "M40", "M60", "M61",
    )
    if any(k in name.upper() for k in gpu_keywords):
        tier = "GPU"
    elif "TPU" in name.upper() or "Tensor Processing Unit" in name:
        tier = "TPU"
    else:
        tier = "CPU"
    # Memory bucket
    if isinstance(vram, (int, float)) and vram > 0:
        if vram < 32:
            bucket = "low"
        elif vram <= 128:
            bucket = "medium"
        else:
            bucket = "high"
    elif isinstance(vram, str) and vram.strip():
        try:
            v = float(vram.replace("GB", "").strip())
            if v < 32:
                bucket = "low"
            elif v <= 128:
                bucket = "medium"
            else:
                bucket = "high"
        except (ValueError, TypeError):
            bucket = "unknown"
    else:
        bucket = "unknown"
    return (tier, bucket)


def _gpu_arch_family(hw: str | dict) -> str:
    """Extract GPU architecture family from hardware data."""
    if not isinstance(hw, dict):
        return "unknown"
    name = hw.get("device_name", "")
    pci = hw.get("device_pci", "")
    # PCI ID mapping for known families
    pci_map = {
        # NVIDIA
        "10de:2204": "Ada Lovelace",   # RTX 4090
        "10de:a000": "Ampere",          # A100
        "10de:2334": "Hopper",          # H100
        "10de:20b2": "Ampere",          # V100
        "10de:2350": "Hopper",          # H200
        # AMD
        "1002:15bf": "AMD RDNA3/Strix Halo",
    }
    if pci in pci_map:
        return pci_map[pci]
    # AMD prefix fallback (vendor 1002)
    if pci.startswith("1002:"):
        return "AMD"
    if any(k in name.upper() for k in ("4090", "4080", "4070", "RTX 5")):
        return "Ada Lovelace"
    if any(k in name.upper() for k in ("A100", "A40")):
        return "Ampere"
    if any(k in name.upper() for k in ("H100", "H200", "H800")):
        return "Hopper"
    if "RDNA" in name.upper() or "RADEON" in name.upper():
        return "AMD"
    return "unknown"


def _hw_cell_html(hw: Any) -> str:
    """Render hardware data as structured HTML for display in the table cell."""
    if not isinstance(hw, dict):
        return html.escape(str(hw))
    parts = []
    name = hw.get("device_name")
    if name:
        parts.append(html.escape(str(name)))
    vram = hw.get("vram_gb")
    if vram is not None:
        parts.append(f"{html.escape(str(vram))} GB")
    pci = hw.get("device_pci")
    if pci:
        parts.append(f"PCI {html.escape(str(pci))}")
    return " · ".join(parts) if parts else html.escape(str(hw))


def render_html(payload: dict[str, Any]) -> str:
    rows = []
    for entry in payload["entries"]:
        model_ids = ", ".join(entry.get("model_ids") or [])
        plain_cells = [
            entry.get("run_id"),
            entry.get("timestamp"),
            entry.get("signer") or "",
            model_ids,
            entry.get("judge_mode") or "",
            entry.get("config_hash") or "",
            entry.get("model_family") or "unknown",
            entry.get("architecture") or "unknown",
            entry.get("params_total") or "unknown",
            (entry.get("params_active") or "—"),
            entry.get("context_length") or "unknown",
            entry.get("quantization") or "unknown",
        ]
        hw = entry.get("hardware") or "unknown"
        cells_html = "".join(f"<td>{html.escape(str(cell))}</td>" for cell in plain_cells)
        cells_html += f"<td>{_hw_cell_html(hw)}</td>"
        # Build data attributes for filtering
        data = {
            "model_family": entry.get("model_family") or "unknown",
            "architecture": entry.get("architecture") or "unknown",
            "quantization": entry.get("quantization") or "unknown",
            "context_length": entry.get("context_length") or "unknown",
            "params_total": entry.get("params_total") or "unknown",
            "hw_tier": _hw_tier(hw)[0],
            "hw_bucket": _hw_tier(hw)[1],
            "hw_arch": _gpu_arch_family(hw),
        }
        attrs = " ".join(f'data-{k}="{html.escape(v, quote=True)}"' for k, v in data.items())
        rows.append(f"<tr {attrs}>{cells_html}</tr>")

    generated_at = html.escape(str(payload["generated_at"]))
    body_rows = "\n".join(rows) if rows else (
        '<tr><td colspan="13">No submissions have been published yet.</td></tr>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rethunk Bakeoff Results</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.5; }}
    input, select {{ margin: 0.5rem 0; max-width: 32rem; padding: 0.5rem; width: 100%; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #ddd; padding: 0.5rem; text-align: left; }}
    th {{ background: #f6f8fa; }}
    th.sortable {{ cursor: pointer; user-select: none; white-space: nowrap; }}
    th.sortable:hover {{ background: #e8eaed; }}
    th.sort-asc::after {{ content: " ▲"; font-size: 0.8em; color: #0969da; }}
    th.sort-desc::after {{ content: " ▼"; font-size: 0.8em; color: #0969da; }}
    .filter-bar {{ margin: 1rem 0; padding: 1rem; background: #f9f9f9; border-radius: 4px; }}
    .filter-bar-header {{ display: flex; justify-content: space-between; align-items: center; }}
    .filter-chevron {{ background: none; border: none; cursor: pointer; font-size: 1rem; padding: 0.1rem 0.4rem; border-radius: 3px; color: #444; line-height: 1; }}
    .filter-chevron:hover {{ background: #e8eaed; }}
    .filter-rows-wrap {{ margin-top: 0.75rem; }}
    .filter-chip-strip {{ display: flex; flex-wrap: wrap; gap: 0.4rem; margin-top: 0.5rem; }}
    .filter-chip {{ display: inline-flex; align-items: center; gap: 0.3rem; background: #ddf4ff; color: #0969da; border: 1px solid #b6daff; border-radius: 12px; padding: 2px 10px; font-size: 0.8em; }}
    .filter-chip-clear {{ background: none; border: none; cursor: pointer; color: #0969da; font-size: 1em; padding: 0; line-height: 1; margin-left: 2px; }}
    .filter-chip-clear:hover {{ color: #cf222e; }}
    .filter-row {{ display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 0.5rem; }}
    .filter-group {{ flex: 1; min-width: 150px; }}
    .filter-group label {{ display: block; font-size: 0.85em; margin-bottom: 0.25rem; }}
    .filter-group-controls {{ display: flex; align-items: center; gap: 0.25rem; }}
    .filter-group-controls select {{ flex: 1; margin: 0; }}
    .filter-add-btn {{ flex-shrink: 0; padding: 0.3rem 0.5rem; background: #f6f8fa; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 0.85em; line-height: 1; }}
    .filter-add-btn:hover {{ background: #e8eaed; }}
    .multi-panel {{ margin-top: 0.3rem; padding: 0.4rem; background: #fff; border: 1px solid #ddd; border-radius: 4px; max-height: 180px; overflow-y: auto; display: none; }}
    .multi-panel.active {{ display: block; }}
    .multi-panel label {{ display: flex; align-items: center; gap: 0.4rem; font-size: 0.85em; margin-bottom: 0.2rem; cursor: pointer; font-weight: normal; }}
    .multi-panel input[type=checkbox] {{ width: auto; margin: 0; padding: 0; }}
    .hw-badge {{ display: inline-block; margin-left: 0.5rem; font-size: 0.8em; color: #58a6ff; background: #ddf4ff; padding: 1px 6px; border-radius: 3px; }}
    .hw-col {{ display: table-cell; }}
    .toggle-btn {{ padding: 0.4rem 0.8rem; background: #f6f8fa; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 0.85em; }}
    .toggle-btn:hover {{ background: #e8eaed; }}
    .table-toolbar {{ display: flex; align-items: center; gap: 0.5rem; margin: 0.5rem 0; flex-wrap: wrap; }}
    .gear-btn {{ padding: 0.4rem 0.6rem; background: #f6f8fa; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 1rem; line-height: 1; }}
    .gear-btn:hover {{ background: #e8eaed; }}
    .col-vis-panel {{ position: absolute; z-index: 100; background: #fff; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); padding: 0.75rem 1rem; min-width: 200px; display: none; }}
    .col-vis-panel.active {{ display: block; }}
    .col-vis-panel h4 {{ margin: 0 0 0.5rem 0; font-size: 0.9em; color: #555; }}
    .col-vis-panel label {{ display: flex; align-items: center; gap: 0.4rem; font-size: 0.85em; margin-bottom: 0.25rem; cursor: pointer; font-weight: normal; }}
    .col-vis-panel input[type=checkbox] {{ width: auto; margin: 0; padding: 0; }}
    @media (max-width: 768px) {{ .filter-row {{ flex-direction: column; }} }}
  </style>
</head>
<body>
  <h1>Rethunk Bakeoff Results</h1>
  <p>Generated at {generated_at}. This static index is backed by validated
  result bundles and is private until publication is approved.</p>
  <div class="filter-bar">
    <div class="filter-bar-header">
      <label><strong>Filter results</strong></label>
      <button class="filter-chevron" id="filter-toggle" aria-label="Toggle filter bar" title="Toggle filters">▼</button>
    </div>
    <div id="filter-chip-strip" class="filter-chip-strip"></div>
    <div id="filter-rows-wrap" class="filter-rows-wrap">
      <div class="filter-row">
        <div class="filter-group" data-filter-id="f-family">
          <label for="f-family">Model Family</label>
          <div class="filter-group-controls">
            <select id="f-family"><option value="">All</option></select>
            <button class="filter-add-btn" data-target="f-family" title="Add value (multi-select)">+</button>
          </div>
          <div class="multi-panel" id="mp-f-family"></div>
        </div>
        <div class="filter-group" data-filter-id="f-arch">
          <label for="f-arch">Architecture</label>
          <div class="filter-group-controls">
            <select id="f-arch"><option value="">All</option></select>
            <button class="filter-add-btn" data-target="f-arch" title="Add value (multi-select)">+</button>
          </div>
          <div class="multi-panel" id="mp-f-arch"></div>
        </div>
        <div class="filter-group" data-filter-id="f-quant">
          <label for="f-quant">Quantization</label>
          <div class="filter-group-controls">
            <select id="f-quant"><option value="">All</option></select>
            <button class="filter-add-btn" data-target="f-quant" title="Add value (multi-select)">+</button>
          </div>
          <div class="multi-panel" id="mp-f-quant"></div>
        </div>
        <div class="filter-group" data-filter-id="f-ctx">
          <label for="f-ctx">Context Length</label>
          <div class="filter-group-controls">
            <select id="f-ctx"><option value="">All</option></select>
            <button class="filter-add-btn" data-target="f-ctx" title="Add value (multi-select)">+</button>
          </div>
          <div class="multi-panel" id="mp-f-ctx"></div>
        </div>
      </div>
      <div class="filter-row">
        <div class="filter-group" data-filter-id="f-params">
          <label for="f-params">Parameter Range</label>
          <div class="filter-group-controls">
            <select id="f-params"><option value="">Any</option></select>
            <button class="filter-add-btn" data-target="f-params" title="Add value (multi-select)">+</button>
          </div>
          <div class="multi-panel" id="mp-f-params"></div>
        </div>
        <div class="filter-group" data-filter-id="f-gpu">
          <label for="f-gpu">GPU Architecture</label>
          <div class="filter-group-controls">
            <select id="f-gpu"><option value="">All</option></select>
            <button class="filter-add-btn" data-target="f-gpu" title="Add value (multi-select)">+</button>
          </div>
          <div class="multi-panel" id="mp-f-gpu"></div>
        </div>
        <div class="filter-group" data-filter-id="f-vram">
          <label for="f-vram">VRAM Tier</label>
          <div class="filter-group-controls">
            <select id="f-vram"><option value="">All</option></select>
            <button class="filter-add-btn" data-target="f-vram" title="Add value (multi-select)">+</button>
          </div>
          <div class="multi-panel" id="mp-f-vram"></div>
        </div>
      </div>
    </div>
  </div>
  <div class="table-toolbar">
    <button class="toggle-btn" id="toggle-hw">Show Hardware</button>
    <div style="position:relative">
      <button class="gear-btn" id="col-vis-btn" title="Column visibility">&#9881;</button>
      <div class="col-vis-panel" id="col-vis-panel">
        <h4>Column visibility</h4>
        <div id="col-vis-list"></div>
      </div>
    </div>
    <button class="toggle-btn" id="clear-all-filters" style="margin-left:auto">Clear all filters</button>
  </div>
  <label for="f-text">Quick search</label>
  <input id="f-text" type="search" placeholder="Filter by run, signer, model, judge mode, config hash, or hardware">
  <table>
    <thead>
      <tr>
        <th class="sortable" data-col-index="0">Run ID</th>
        <th class="sortable" data-col-index="1">Timestamp</th>
        <th class="sortable" data-col-index="2">Signer</th>
        <th class="sortable" data-col-index="3">Models</th>
        <th class="sortable" data-col-index="4">Judge Mode</th>
        <th class="sortable" data-col-index="5">Config Hash</th>
        <th class="sortable" data-col-index="6">Model Family</th>
        <th class="sortable" data-col-index="7">Architecture</th>
        <th class="sortable" data-col-index="8">Params (total)</th>
        <th class="sortable" data-col-index="9">Params (active)</th>
        <th class="sortable" data-col-index="10">Context Len</th>
        <th class="sortable" data-col-index="11">Quantization</th>
        <th class="hw-col sortable" data-col-index="12" id="hw-col-header" style="display:none">Hardware</th>
      </tr>
    </thead>
    <tbody id="results">
{body_rows}
    </tbody>
  </table>
  <p id="no-results" style="display:none">No results match selected filters.</p>
  <script>
    const fText = document.getElementById("f-text");
    const tbody = document.getElementById("results");
    const rows = Array.from(document.querySelectorAll("#results tr"));
    const defaultRowOrder = [...rows];
    const hwCol = document.getElementById("hw-col-header");
    const noResults = document.getElementById("no-results");
    let hwVisible = false;

    // --- Sort state ---
    let sortState = {{ col: null, dir: null }};
    try {{
      const stored = JSON.parse(localStorage.getItem("bakeoff_sort") || "null");
      if (stored && typeof stored.col === "number") sortState = stored;
    }} catch(e) {{}}

    // --- Column visibility state ---
    const COL_COUNT = 13;
    const FILTER_TO_COL = {{ "f-family": 6, "f-arch": 7, "f-quant": 11, "f-ctx": 10, "f-params": 8 }};
    let colVisible = {{}};
    let colOverride = new Set();
    try {{
      const sv = JSON.parse(localStorage.getItem("bakeoff_col_visible") || "null");
      if (sv && typeof sv === "object") colVisible = sv;
      const so = JSON.parse(localStorage.getItem("bakeoff_col_override") || "null");
      if (Array.isArray(so)) colOverride = new Set(so);
    }} catch(e) {{}}
    // Default: all visible
    for (let i = 0; i < COL_COUNT; i++) {{
      if (!(i in colVisible)) colVisible[i] = true;
    }}
    // Hardware column controlled separately via toggle-hw
    colVisible[12] = hwVisible;

    // --- Multi-select filter state ---
    // filterMode: 'single' | 'multi' per filter id
    // filterValues: array of selected values when in multi mode
    const FILTER_IDS = ["f-family", "f-arch", "f-quant", "f-ctx", "f-params", "f-gpu", "f-vram"];
    let filterMode = {{}};
    let filterValues = {{}};
    try {{
      const fm = JSON.parse(localStorage.getItem("bakeoff_filter_mode") || "null");
      if (fm && typeof fm === "object") filterMode = fm;
      const fv = JSON.parse(localStorage.getItem("bakeoff_filter_values") || "null");
      if (fv && typeof fv === "object") filterValues = fv;
    }} catch(e) {{}}
    FILTER_IDS.forEach(id => {{
      if (!filterMode[id]) filterMode[id] = "single";
      if (!filterValues[id]) filterValues[id] = [];
    }});

    function saveFilterState() {{
      try {{
        localStorage.setItem("bakeoff_filter_mode", JSON.stringify(filterMode));
        localStorage.setItem("bakeoff_filter_values", JSON.stringify(filterValues));
      }} catch(e) {{}}
    }}

    // Populate dropdowns from data attributes
    function populateSelect(id, dataKey) {{
      const sel = document.getElementById(id);
      const vals = new Set();
      rows.forEach(r => {{
        const v = (r.dataset[dataKey] || "").toLowerCase();
        if (v && v !== "unknown" && v !== "—") vals.add(v);
      }});
      [...vals].sort().forEach(v => {{
        const opt = document.createElement("option");
        opt.value = v; opt.textContent = v;
        sel.appendChild(opt);
      }});
    }}
    function parseParams(v) {{
      if (!v || v === "—") return NaN;
      const m = v.match(/([\\d.]+)/);
      return m ? parseFloat(m[1]) : NaN;
    }}
    function paramsRange(p) {{
      if (isNaN(p)) return "unknown";
      if (p <= 1.5) return "1.5";
      if (p <= 3) return "3";
      if (p <= 7) return "7";
      if (p <= 13) return "13";
      return "34+";
    }}
    function ctxRange(c) {{
      if (!c || c === "unknown") return "unknown";
      const m = c.match(/([\\d]+)/);
      if (!m) return "unknown";
      const v = parseInt(m[1]);
      if (v <= 8192) return "1K-8K";
      if (v <= 32768) return "8K-32K";
      if (v <= 131072) return "32K-128K";
      return "128K+";
    }}
    function vramBucket(hw) {{
      if (typeof hw !== "object" || !hw) return "unknown";
      const v = hw.vram_gb;
      if (typeof v === "number" && v > 0) {{
        if (v < 32) return "low";
        if (v <= 128) return "medium";
        return "high";
      }}
      return "unknown";
    }}

    // Populate computed-value dropdowns
    function populateComputed(id, computeFn) {{
      const sel = document.getElementById(id);
      const vals = new Set();
      rows.forEach(r => {{
        const v = computeFn(r);
        if (v && v !== "unknown") vals.add(v);
      }});
      [...vals].sort().forEach(v => {{
        const opt = document.createElement("option");
        opt.value = v; opt.textContent = v;
        sel.appendChild(opt);
      }});
    }}

    // Build multi-panel checkboxes from a select element's options
    function buildMultiPanel(id) {{
      const sel = document.getElementById(id);
      const panel = document.getElementById("mp-" + id);
      if (!panel) return;
      panel.innerHTML = "";
      // Skip first "All" option
      Array.from(sel.options).slice(1).forEach(opt => {{
        const lbl = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.value = opt.value;
        cb.checked = filterValues[id].includes(opt.value);
        cb.addEventListener("change", () => {{
          if (cb.checked) {{
            if (!filterValues[id].includes(cb.value)) filterValues[id].push(cb.value);
          }} else {{
            filterValues[id] = filterValues[id].filter(v => v !== cb.value);
          }}
          saveFilterState();
          applyFilters();
          renderChips();
        }});
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(" " + opt.textContent));
        panel.appendChild(lbl);
      }});
    }}

    function switchToMulti(id) {{
      filterMode[id] = "multi";
      // Seed multi-values from current single-select value
      const sel = document.getElementById(id);
      if (sel.value && !filterValues[id].includes(sel.value)) {{
        filterValues[id].push(sel.value);
      }}
      sel.value = "";
      sel.style.display = "none";
      const panel = document.getElementById("mp-" + id);
      if (panel) {{
        buildMultiPanel(id);
        panel.classList.add("active");
      }}
      saveFilterState();
      applyFilters();
      renderChips();
    }}

    function switchToSingle(id) {{
      filterMode[id] = "single";
      filterValues[id] = [];
      const sel = document.getElementById(id);
      sel.value = "";
      sel.style.display = "";
      const panel = document.getElementById("mp-" + id);
      if (panel) panel.classList.remove("active");
      saveFilterState();
      applyFilters();
      renderChips();
    }}

    // Restore multi mode from localStorage on load
    function restoreFilterModes() {{
      FILTER_IDS.forEach(id => {{
        if (filterMode[id] === "multi") {{
          const sel = document.getElementById(id);
          if (sel) sel.style.display = "none";
          const panel = document.getElementById("mp-" + id);
          if (panel) {{
            buildMultiPanel(id);
            panel.classList.add("active");
          }}
        }}
      }});
    }}

    // "+" button click handler — toggle single/multi
    document.querySelectorAll(".filter-add-btn").forEach(btn => {{
      btn.addEventListener("click", () => {{
        const id = btn.dataset.target;
        if (filterMode[id] === "multi") {{
          switchToSingle(id);
        }} else {{
          switchToMulti(id);
        }}
      }});
    }});

    // Populate all filter dropdowns
    populateSelect("f-family", "model_family");
    populateSelect("f-arch", "architecture");
    populateSelect("f-quant", "quantization");
    populateComputed("f-ctx",    r => ctxRange(r.dataset.context_length || ""));
    populateComputed("f-params", r => paramsRange(parseParams(r.dataset.params_total || "")));
    populateComputed("f-gpu",    r => r.dataset.hw_arch || "");
    populateComputed("f-vram",   r => r.dataset.hw_bucket || "");

    // Build all multi-panels after options are populated
    FILTER_IDS.forEach(id => buildMultiPanel(id));
    restoreFilterModes();

    // Helper: get active values for a filter (works for both modes)
    function getActiveValues(id) {{
      if (filterMode[id] === "multi") {{
        return filterValues[id] || [];
      }}
      const sel = document.getElementById(id);
      return sel && sel.value ? [sel.value] : [];
    }}

    // Row value extractor per filter id
    function rowValueForFilter(row, id) {{
      if (id === "f-family") return (row.dataset.model_family || "").toLowerCase();
      if (id === "f-arch") return (row.dataset.architecture || "").toLowerCase();
      if (id === "f-quant") return (row.dataset.quantization || "").toLowerCase();
      if (id === "f-ctx") return ctxRange(row.dataset.context_length || "");
      if (id === "f-params") return paramsRange(parseParams(row.dataset.params_total || "0"));
      if (id === "f-gpu") return (row.dataset.hw_arch || "").toLowerCase();
      if (id === "f-vram") return row.dataset.hw_bucket || "";
      return "";
    }}

    // Multi-dimensional filter with OR within dimension, AND across
    function applyFilters() {{
      let visible = 0;
      rows.forEach(row => {{
        const text = row.textContent.toLowerCase();
        const query = fText.value.toLowerCase();
        const matchText = !query || text.includes(query);

        let match = true;
        FILTER_IDS.forEach(id => {{
          const activeVals = getActiveValues(id);
          if (activeVals.length === 0) return; // no filter active
          const rowVal = rowValueForFilter(row, id);
          // OR logic within dimension
          const dimMatch = activeVals.some(v => rowVal === v.toLowerCase());
          if (!dimMatch) match = false;
        }});

        const visible_row = matchText && match;
        row.hidden = !visible_row;
        if (visible_row) visible++;
      }});
      noResults.style.display = visible === 0 ? "block" : "none";
      updateAutoHide();
    }}

    // Hardware badge rendering
    function renderBadges() {{
      const groups = {{}};
      rows.forEach(row => {{
        if (row.hidden) return;
        const tier = row.dataset.hw_tier || "unknown";
        const bucket = row.dataset.hw_bucket || "unknown";
        const key = tier + "|" + bucket;
        if (!(key in groups)) groups[key] = [];
        groups[key].push(row);
      }});
      rows.forEach(row => {{
        const badge = row.querySelector(".hw-badge");
        if (badge) badge.remove();
        const tier = row.dataset.hw_tier || "unknown";
        const bucket = row.dataset.hw_bucket || "unknown";
        const key = tier + "|" + bucket;
        if (groups[key] && groups[key].length > 1) {{
          const span = document.createElement("span");
          span.className = "hw-badge";
          span.textContent = (groups[key].length - 1) + " other result(s) on similar hardware";
          row.cells[0].appendChild(span);
        }}
      }});
    }}

    // GPU arch from PCI
    function _gpu_arch_family(hw_str) {{
      if (typeof hw_str !== "string") return "unknown";
      const pci = "10de:2204";
      if (hw_str.includes(pci)) return "Ada Lovelace";
      if (hw_str.includes("10de:a000")) return "Ampere";
      if (hw_str.includes("10de:2334")) return "Hopper";
      if (hw_str.includes("10de:20b2")) return "Ampere";
      if (hw_str.includes("H100") || hw_str.includes("H200")) return "Hopper";
      if (hw_str.includes("A100")) return "Ampere";
      return "unknown";
    }}

    // Toggle hardware column
    document.getElementById("toggle-hw").addEventListener("click", () => {{
      hwVisible = !hwVisible;
      hwCol.style.display = hwVisible ? "table-cell" : "none";
      rows.forEach(r => {{
        const cells = r.querySelectorAll("td");
        if (cells.length > 0) cells[cells.length - 1].style.display = hwVisible ? "table-cell" : "none";
      }});
      colVisible[12] = hwVisible;
      try {{ localStorage.setItem("hw_visible", hwVisible); }} catch(e) {{}}
    }});
    try {{
      if (localStorage.getItem("hw_visible") === "true") {{
        document.getElementById("toggle-hw").click();
      }}
    }} catch(e) {{}}

    // --- Column visibility panel (#17) ---
    const COL_NAMES = [
      "Run ID", "Timestamp", "Signer", "Models", "Judge Mode", "Config Hash",
      "Model Family", "Architecture", "Params (total)", "Params (active)",
      "Context Len", "Quantization", "Hardware"
    ];

    function saveColState() {{
      try {{
        localStorage.setItem("bakeoff_col_visible", JSON.stringify(colVisible));
        localStorage.setItem("bakeoff_col_override", JSON.stringify([...colOverride]));
      }} catch(e) {{}}
    }}

    function applyColVisibility() {{
      const ths = document.querySelectorAll("thead th[data-col-index]");
      ths.forEach(th => {{
        const idx = parseInt(th.dataset.colIndex);
        if (idx === 12) return; // hardware handled by toggle-hw
        const show = colVisible[idx] !== false;
        th.style.display = show ? "" : "none";
      }});
      rows.forEach(row => {{
        const cells = row.querySelectorAll("td");
        cells.forEach((td, idx) => {{
          if (idx === 12) return; // hardware handled by toggle-hw
          td.style.display = colVisible[idx] !== false ? "" : "none";
        }});
      }});
    }}

    function buildColVisPanel() {{
      const list = document.getElementById("col-vis-list");
      list.innerHTML = "";
      for (let i = 0; i < COL_COUNT - 1; i++) {{ // skip hardware (12), handled by toggle
        const lbl = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.dataset.colIdx = i;
        cb.checked = colVisible[i] !== false;
        cb.addEventListener("change", () => {{
          colVisible[i] = cb.checked;
          colOverride.add(i); // user explicitly set this column
          saveColState();
          applyColVisibility();
        }});
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(" " + COL_NAMES[i]));
        list.appendChild(lbl);
      }}
    }}

    // Auto-hide rule: if a filter for field X is set to exactly one value (single-select), auto-hide that column
    // unless user has explicitly overridden it
    function updateAutoHide() {{
      Object.entries(FILTER_TO_COL).forEach(([filterId, colIdx]) => {{
        if (colOverride.has(colIdx)) return; // user override wins
        const activeVals = getActiveValues(filterId);
        const singleActive = (filterMode[filterId] === "single")
          ? (document.getElementById(filterId) && document.getElementById(filterId).value !== "")
          : false;
        // Auto-hide only fires for single-select with exactly one value
        if (singleActive) {{
          colVisible[colIdx] = false;
        }} else {{
          colVisible[colIdx] = true;
        }}
      }});
      saveColState();
      applyColVisibility();
      // Sync checkboxes in panel
      document.querySelectorAll("#col-vis-list input[type=checkbox]").forEach(cb => {{
        const idx = parseInt(cb.dataset.colIdx);
        cb.checked = colVisible[idx] !== false;
      }});
    }}

    // Gear button toggle
    const colVisPanel = document.getElementById("col-vis-panel");
    document.getElementById("col-vis-btn").addEventListener("click", (e) => {{
      e.stopPropagation();
      colVisPanel.classList.toggle("active");
    }});
    document.addEventListener("click", () => colVisPanel.classList.remove("active"));
    colVisPanel.addEventListener("click", e => e.stopPropagation());

    buildColVisPanel();
    applyColVisibility();

    // --- Sorting (#18) ---
    function getCellValue(row, colIdx) {{
      const cells = row.querySelectorAll("td");
      if (!cells[colIdx]) return "";
      return cells[colIdx].textContent.trim();
    }}

    function compareValues(a, b, colIdx) {{
      // Timestamp column (1): ISO string sort
      if (colIdx === 1) return a.localeCompare(b);
      // Numeric columns: params total (8), context len (10)
      if (colIdx === 8 || colIdx === 9) {{
        const na = parseParams(a), nb = parseParams(b);
        if (!isNaN(na) && !isNaN(nb)) return na - nb;
        if (!isNaN(na)) return -1;
        if (!isNaN(nb)) return 1;
        return a.localeCompare(b);
      }}
      if (colIdx === 10) {{
        const ma = a.match(/([\\d]+)/), mb = b.match(/([\\d]+)/);
        const na = ma ? parseInt(ma[1]) : NaN;
        const nb = mb ? parseInt(mb[1]) : NaN;
        if (!isNaN(na) && !isNaN(nb)) return na - nb;
        if (!isNaN(na)) return -1;
        if (!isNaN(nb)) return 1;
        return a.localeCompare(b);
      }}
      return a.localeCompare(b);
    }}

    function applySort() {{
      const ths = document.querySelectorAll("thead th.sortable");
      ths.forEach(th => {{
        th.classList.remove("sort-asc", "sort-desc");
        if (sortState.col !== null && parseInt(th.dataset.colIndex) === sortState.col) {{
          th.classList.add(sortState.dir === "asc" ? "sort-asc" : "sort-desc");
        }}
      }});
      if (sortState.col === null || sortState.dir === null) {{
        // Restore default order
        defaultRowOrder.forEach(row => tbody.appendChild(row));
        return;
      }}
      const col = sortState.col;
      const dir = sortState.dir;
      const sorted = [...rows].sort((a, b) => {{
        const av = getCellValue(a, col);
        const bv = getCellValue(b, col);
        const cmp = compareValues(av, bv, col);
        return dir === "asc" ? cmp : -cmp;
      }});
      sorted.forEach(row => tbody.appendChild(row));
    }}

    document.querySelectorAll("thead th.sortable").forEach(th => {{
      th.addEventListener("click", () => {{
        const col = parseInt(th.dataset.colIndex);
        if (sortState.col === col) {{
          if (sortState.dir === "asc") sortState.dir = "desc";
          else if (sortState.dir === "desc") {{ sortState.col = null; sortState.dir = null; }}
          else {{ sortState.dir = "asc"; }}
        }} else {{
          sortState.col = col;
          sortState.dir = "asc";
        }}
        try {{ localStorage.setItem("bakeoff_sort", JSON.stringify(sortState)); }} catch(e) {{}}
        applySort();
      }});
    }});

    applySort();

    // Collapsible filter bar
    const filterRowsWrap = document.getElementById("filter-rows-wrap");
    const filterChipStrip = document.getElementById("filter-chip-strip");
    const filterToggleBtn = document.getElementById("filter-toggle");

    const FILTER_LABELS = {{
      "f-family": "Model Family",
      "f-arch": "Architecture",
      "f-quant": "Quantization",
      "f-ctx": "Context Length",
      "f-params": "Parameter Range",
      "f-gpu": "GPU Architecture",
      "f-vram": "VRAM Tier"
    }};

    function renderChips() {{
      filterChipStrip.innerHTML = "";
      const expanded = filterRowsWrap.style.display !== "none";
      if (expanded) return;
      FILTER_IDS.forEach(id => {{
        const activeVals = getActiveValues(id);
        if (activeVals.length === 0) return;
        const chip = document.createElement("span");
        chip.className = "filter-chip";
        const label = document.createElement("span");
        label.textContent = FILTER_LABELS[id] + ": ";
        const val = document.createElement("strong");
        if (filterMode[id] === "multi" && activeVals.length > 1) {{
          val.textContent = "[" + activeVals.length + " selected]";
        }} else {{
          val.textContent = activeVals[0];
        }}
        chip.appendChild(label);
        chip.appendChild(val);
        const clearBtn = document.createElement("button");
        clearBtn.className = "filter-chip-clear";
        clearBtn.textContent = "×";
        clearBtn.title = "Clear " + FILTER_LABELS[id];
        clearBtn.addEventListener("click", () => {{
          if (filterMode[id] === "multi") {{
            switchToSingle(id);
          }} else {{
            const sel = document.getElementById(id);
            if (sel) sel.value = "";
          }}
          applyFilters();
          renderChips();
        }});
        chip.appendChild(clearBtn);
        filterChipStrip.appendChild(chip);
      }});
    }}

    function setFilterBarExpanded(expanded) {{
      filterRowsWrap.style.display = expanded ? "" : "none";
      filterToggleBtn.textContent = expanded ? "▲" : "▼";
      filterToggleBtn.title = expanded ? "Collapse filters" : "Expand filters";
      try {{ localStorage.setItem("filter_bar_expanded", expanded ? "true" : "false"); }} catch(e) {{}}
      renderChips();
    }}

    filterToggleBtn.addEventListener("click", () => {{
      const expanded = filterRowsWrap.style.display !== "none";
      setFilterBarExpanded(!expanded);
    }});

    let initExpanded = false;
    try {{
      const stored = localStorage.getItem("filter_bar_expanded");
      if (stored === "true") initExpanded = true;
    }} catch(e) {{}}
    setFilterBarExpanded(initExpanded);

    // Clear all filters
    document.getElementById("clear-all-filters").addEventListener("click", () => {{
      FILTER_IDS.forEach(id => {{
        if (filterMode[id] === "multi") {{
          switchToSingle(id);
        }} else {{
          const sel = document.getElementById(id);
          if (sel) sel.value = "";
        }}
      }});
      fText.value = "";
      applyFilters();
      renderChips();
    }});

    // Bind single-select filter inputs
    FILTER_IDS.forEach(id => {{
      const el = document.getElementById(id);
      if (el) {{
        el.addEventListener("input", () => {{ applyFilters(); renderChips(); }});
        el.addEventListener("change", () => {{ applyFilters(); renderChips(); }});
      }}
    }});
    fText.addEventListener("input", () => {{ applyFilters(); renderChips(); }});
    fText.addEventListener("change", () => {{ applyFilters(); renderChips(); }});

    applyFilters();
    renderBadges();
  </script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submissions", type=Path, default=Path("submissions"))
    parser.add_argument("--site", type=Path, default=Path("site"))
    args = parser.parse_args(argv)

    payload = build_index(args.submissions, args.site)
    print(f"Wrote {args.site / 'index.json'} with {len(payload['entries'])} entries.")
    print(f"Wrote {args.site / 'index.html'}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
