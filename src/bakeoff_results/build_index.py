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
    """Classify hardware into tier + vram_mb (for range filtering)."""
    if not isinstance(hw, dict):
        return ("unknown", -1)
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
    # Convert vram_gb to mb integer for range filtering
    vram_mb = -1
    if isinstance(vram, (int, float)) and vram > 0:
        vram_mb = int(float(vram) * 1024)
    elif isinstance(vram, str) and vram.strip():
        try:
            v = float(vram.replace("GB", "").strip())
            vram_mb = int(v * 1024)
        except (ValueError, TypeError):
            vram_mb = -1
    return (tier, vram_mb)


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
        config_hash = entry.get("config_hash") or ""
        hw = entry.get("hardware") or "unknown"
        hw_tier_val, hw_vram_mb = _hw_tier(hw)

        # Columns (no config_hash in main columns — moved to row detail)
        # Col indices: 0=Run ID, 1=Timestamp, 2=Signer, 3=Models, 4=Judge Mode,
        #              5=Model Family, 6=Architecture, 7=Params(total), 8=Params(active),
        #              9=Context Len, 10=Quantization, 11=Similar Results (hidden), 12=Hardware
        plain_cells = [
            entry.get("run_id"),
            entry.get("timestamp"),
            entry.get("signer") or "",
            model_ids,
            entry.get("judge_mode") or "",
            entry.get("model_family") or "unknown",
            entry.get("architecture") or "unknown",
            entry.get("params_total") or "unknown",
            (entry.get("params_active") or "—"),
            entry.get("context_length") or "unknown",
            entry.get("quantization") or "unknown",
        ]
        cells_html = "".join(f"<td>{html.escape(str(cell))}</td>" for cell in plain_cells)
        # Similar Results column (hidden by default — JS will populate badge content)
        cells_html += '<td class="similar-results-col" style="display:none"></td>'
        cells_html += f"<td class='hw-col-td'>{_hw_cell_html(hw)}</td>"

        # Config hash in a detail row (click-to-copy)
        cfg_escaped = html.escape(config_hash, quote=True)
        detail_html = ""
        if config_hash:
            detail_html = (
                f'<tr class="row-detail" style="display:none">'
                f'<td colspan="14" style="background:#f6f8fa;padding:0.4rem 0.75rem;font-size:0.85em">'
                f'Config hash: <code id="cfg-{cfg_escaped[:8]}" style="font-size:0.95em">{html.escape(config_hash)}</code>'
                f'<button class="copy-btn" data-copy="{cfg_escaped}" '
                f'style="margin-left:0.5rem;padding:1px 6px;font-size:0.8em;cursor:pointer;'
                f'background:#f6f8fa;border:1px solid #ccc;border-radius:3px" '
                f'title="Copy config hash">copy</button>'
                f'</td></tr>'
            )

        # Build data attributes for filtering
        data = {
            "model_family": entry.get("model_family") or "unknown",
            "architecture": entry.get("architecture") or "unknown",
            "quantization": entry.get("quantization") or "unknown",
            "context_length": entry.get("context_length") or "unknown",
            "params_total": entry.get("params_total") or "unknown",
            "hw_tier": hw_tier_val,
            "hw_vram_mb": str(hw_vram_mb),
            "hw_arch": _gpu_arch_family(hw),
            "config_hash": config_hash,
        }
        str_data = {k: str(v) for k, v in data.items()}
        attrs = " ".join(f'data-{k}="{html.escape(v, quote=True)}"' for k, v in str_data.items())
        rows.append(f"<tr class='data-row' {attrs}>{cells_html}</tr>{detail_html}")

    generated_at = html.escape(str(payload["generated_at"]))
    body_rows = "\n".join(rows) if rows else (
        '<tr><td colspan="14">No submissions have been published yet.</td></tr>'
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
    .filter-bar-header-left {{ display: flex; align-items: center; gap: 0.5rem; }}
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
    .similar-badge {{ display: inline-block; font-size: 0.8em; color: #58a6ff; background: #ddf4ff; padding: 1px 6px; border-radius: 3px; }}
    .toggle-btn {{ padding: 0.4rem 0.8rem; background: #f6f8fa; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 0.85em; }}
    .toggle-btn:hover {{ background: #e8eaed; }}
    .clear-all-btn {{ padding: 0.3rem 0.7rem; background: #f6f8fa; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 0.85em; }}
    .clear-all-btn:hover {{ background: #fee8e8; border-color: #f5a5a5; }}
    .filter-footer {{ display: flex; justify-content: flex-end; margin-top: 0.5rem; }}
    .table-toolbar {{ display: flex; align-items: center; gap: 0.5rem; margin: 0.5rem 0; flex-wrap: wrap; }}
    .gear-btn {{ padding: 0.4rem 0.6rem; background: #f6f8fa; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 1rem; line-height: 1; }}
    .gear-btn:hover {{ background: #e8eaed; }}
    .col-vis-panel {{ position: absolute; right: 0; z-index: 100; background: #fff; border: 1px solid #ccc; border-radius: 6px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); padding: 0.75rem 1rem; min-width: 220px; display: none; }}
    .col-vis-panel.active {{ display: block; }}
    .col-vis-panel h4 {{ margin: 0 0 0.5rem 0; font-size: 0.9em; color: #555; }}
    .col-vis-panel label {{ display: flex; align-items: center; gap: 0.4rem; font-size: 0.85em; margin-bottom: 0.25rem; cursor: pointer; font-weight: normal; }}
    .col-vis-panel input[type=checkbox] {{ width: auto; margin: 0; padding: 0; }}
    .col-vis-panel hr {{ margin: 0.4rem 0; border: none; border-top: 1px solid #eee; }}
    /* Range slider styles */
    .slider-group {{ flex: 1; min-width: 180px; }}
    .slider-group label {{ display: block; font-size: 0.85em; margin-bottom: 0.25rem; }}
    .slider-wrap {{ display: flex; flex-direction: column; gap: 0.3rem; }}
    .slider-row {{ display: flex; align-items: center; gap: 0.5rem; font-size: 0.8em; }}
    .slider-row input[type=range] {{ flex: 1; margin: 0; padding: 0; max-width: none; width: auto; height: 4px; cursor: pointer; }}
    .slider-row span {{ min-width: 3.5rem; text-align: right; color: #555; white-space: nowrap; }}
    @media (max-width: 768px) {{ .filter-row {{ flex-direction: column; }} }}
  </style>
</head>
<body>
  <h1>Rethunk Bakeoff Results</h1>
  <p>Generated at {generated_at}. This static index is backed by validated
  result bundles and is private until publication is approved.</p>
  <div class="filter-bar">
    <div class="filter-bar-header">
      <div class="filter-bar-header-left">
        <label><strong>Filter results</strong></label>
        <button class="filter-chevron" id="filter-toggle" aria-label="Toggle filter bar" title="Toggle filters">▼</button>
      </div>
      <div style="position:relative">
        <button class="gear-btn" id="col-vis-btn" title="Column visibility &amp; display options">&#9881;</button>
        <div class="col-vis-panel" id="col-vis-panel">
          <h4>Column visibility</h4>
          <div id="col-vis-list"></div>
          <hr>
          <label style="margin-top:0.25rem">
            <input type="checkbox" id="toggle-hw-check"> Show Hardware column
          </label>
        </div>
      </div>
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
        <div class="filter-group" data-filter-id="f-gpu">
          <label for="f-gpu">GPU Architecture</label>
          <div class="filter-group-controls">
            <select id="f-gpu"><option value="">All</option></select>
            <button class="filter-add-btn" data-target="f-gpu" title="Add value (multi-select)">+</button>
          </div>
          <div class="multi-panel" id="mp-f-gpu"></div>
        </div>
      </div>
      <div class="filter-row">
        <!-- Params slider (log-scale snap: 1, 3, 7, 13, 30, 70, 400 B) -->
        <div class="slider-group">
          <label>Total Params (B)</label>
          <div class="slider-wrap">
            <div class="slider-row">
              <span>Min:</span>
              <input type="range" id="params-min" min="0" max="6" step="1" value="0">
              <span id="params-min-lbl">1B</span>
            </div>
            <div class="slider-row">
              <span>Max:</span>
              <input type="range" id="params-max" min="0" max="6" step="1" value="6">
              <span id="params-max-lbl">400B</span>
            </div>
          </div>
        </div>
        <!-- Context slider (powers-of-2: 4K, 8K, 16K, 32K, 128K+) -->
        <div class="slider-group">
          <label>Context Length</label>
          <div class="slider-wrap">
            <div class="slider-row">
              <span>Min:</span>
              <input type="range" id="ctx-min" min="0" max="4" step="1" value="0">
              <span id="ctx-min-lbl">4K</span>
            </div>
            <div class="slider-row">
              <span>Max:</span>
              <input type="range" id="ctx-max" min="0" max="4" step="1" value="4">
              <span id="ctx-max-lbl">128K+</span>
            </div>
          </div>
        </div>
        <!-- VRAM slider (GB ranges: 0-8, 8-16, 16-24, 24-40, 40+) -->
        <div class="slider-group">
          <label>VRAM (GB)</label>
          <div class="slider-wrap">
            <div class="slider-row">
              <span>Min:</span>
              <input type="range" id="vram-min" min="0" max="4" step="1" value="0">
              <span id="vram-min-lbl">0 GB</span>
            </div>
            <div class="slider-row">
              <span>Max:</span>
              <input type="range" id="vram-max" min="0" max="4" step="1" value="4">
              <span id="vram-max-lbl">40+ GB</span>
            </div>
          </div>
        </div>
      </div>
      <div class="filter-footer">
        <button class="clear-all-btn" id="clear-all-filters">Clear All</button>
      </div>
    </div>
  </div>
  <div class="table-toolbar">
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
        <th class="sortable" data-col-index="5">Model Family</th>
        <th class="sortable" data-col-index="6">Architecture</th>
        <th class="sortable" data-col-index="7">Params (total)</th>
        <th class="sortable" data-col-index="8">Params (active)</th>
        <th class="sortable" data-col-index="9">Context Len</th>
        <th class="sortable" data-col-index="10">Quantization</th>
        <th class="sortable" data-col-index="11" id="similar-col-header" style="display:none">Similar Results</th>
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
    // Only data rows (not detail rows)
    const rows = Array.from(document.querySelectorAll("#results tr.data-row"));
    const defaultRowOrder = [...rows];
    const hwCol = document.getElementById("hw-col-header");
    const similarColHeader = document.getElementById("similar-col-header");
    const noResults = document.getElementById("no-results");
    let hwVisible = false;

    // --- Params snap points ---
    const PARAMS_SNAPS = [1, 3, 7, 13, 30, 70, 400];
    const PARAMS_LABELS = ["1B", "3B", "7B", "13B", "30B", "70B", "400B"];
    const CTX_SNAPS = [4096, 8192, 16384, 32768, 131072];
    const CTX_LABELS = ["4K", "8K", "16K", "32K", "128K+"];
    // VRAM GB range boundaries: index → lower bound (GB); index 4 = 40+
    const VRAM_SNAPS = [0, 8, 16, 24, 40];
    const VRAM_LABELS = ["0 GB", "8 GB", "16 GB", "24 GB", "40+ GB"];

    // --- Sort state ---
    let sortState = {{ col: null, dir: null }};
    try {{
      const stored = JSON.parse(localStorage.getItem("bakeoff_sort") || "null");
      if (stored && typeof stored.col === "number") sortState = stored;
    }} catch(e) {{}}

    // --- Column visibility state ---
    // Cols: 0=Run ID, 1=Timestamp, 2=Signer, 3=Models, 4=Judge Mode,
    //       5=Model Family, 6=Architecture, 7=Params(total), 8=Params(active),
    //       9=Context Len, 10=Quantization, 11=Similar Results (hidden default), 12=Hardware
    const COL_COUNT = 13;
    const FILTER_TO_COL = {{ "f-family": 5, "f-arch": 6, "f-quant": 10, "f-ctx": 9, "f-params": 7 }};
    let colVisible = {{}};
    let colOverride = new Set();
    try {{
      const sv = JSON.parse(localStorage.getItem("bakeoff_col_visible") || "null");
      if (sv && typeof sv === "object") colVisible = sv;
      const so = JSON.parse(localStorage.getItem("bakeoff_col_override") || "null");
      if (Array.isArray(so)) colOverride = new Set(so);
    }} catch(e) {{}}
    // Default: all visible except Similar Results (11) and Hardware (12)
    for (let i = 0; i < COL_COUNT; i++) {{
      if (!(i in colVisible)) colVisible[i] = (i !== 11 && i !== 12);
    }}
    colVisible[12] = hwVisible;

    // --- Slider state ---
    let sliderState = {{ paramsMin: 0, paramsMax: 6, ctxMin: 0, ctxMax: 4, vramMin: 0, vramMax: 4 }};
    try {{
      const ss = JSON.parse(localStorage.getItem("bakeoff_sliders") || "null");
      if (ss && typeof ss === "object") sliderState = Object.assign(sliderState, ss);
    }} catch(e) {{}}

    function saveSliderState() {{
      try {{ localStorage.setItem("bakeoff_sliders", JSON.stringify(sliderState)); }} catch(e) {{}}
    }}

    // Wire up sliders
    function initSliders() {{
      const paramsMin = document.getElementById("params-min");
      const paramsMax = document.getElementById("params-max");
      const ctxMin = document.getElementById("ctx-min");
      const ctxMax = document.getElementById("ctx-max");
      const vramMin = document.getElementById("vram-min");
      const vramMax = document.getElementById("vram-max");

      paramsMin.value = sliderState.paramsMin;
      paramsMax.value = sliderState.paramsMax;
      ctxMin.value = sliderState.ctxMin;
      ctxMax.value = sliderState.ctxMax;
      vramMin.value = sliderState.vramMin;
      vramMax.value = sliderState.vramMax;

      function updateSliderLabels() {{
        document.getElementById("params-min-lbl").textContent = PARAMS_LABELS[parseInt(paramsMin.value)];
        document.getElementById("params-max-lbl").textContent = PARAMS_LABELS[parseInt(paramsMax.value)];
        document.getElementById("ctx-min-lbl").textContent = CTX_LABELS[parseInt(ctxMin.value)];
        document.getElementById("ctx-max-lbl").textContent = CTX_LABELS[parseInt(ctxMax.value)];
        document.getElementById("vram-min-lbl").textContent = VRAM_LABELS[parseInt(vramMin.value)];
        document.getElementById("vram-max-lbl").textContent = VRAM_LABELS[parseInt(vramMax.value)];
      }}

      function onSliderChange() {{
        // Enforce min <= max
        if (parseInt(paramsMin.value) > parseInt(paramsMax.value)) paramsMax.value = paramsMin.value;
        if (parseInt(ctxMin.value) > parseInt(ctxMax.value)) ctxMax.value = ctxMin.value;
        if (parseInt(vramMin.value) > parseInt(vramMax.value)) vramMax.value = vramMin.value;
        sliderState.paramsMin = parseInt(paramsMin.value);
        sliderState.paramsMax = parseInt(paramsMax.value);
        sliderState.ctxMin = parseInt(ctxMin.value);
        sliderState.ctxMax = parseInt(ctxMax.value);
        sliderState.vramMin = parseInt(vramMin.value);
        sliderState.vramMax = parseInt(vramMax.value);
        updateSliderLabels();
        saveSliderState();
        applyFilters();
        renderChips();
      }}

      [paramsMin, paramsMax, ctxMin, ctxMax, vramMin, vramMax].forEach(el => {{
        el.addEventListener("input", onSliderChange);
      }});
      updateSliderLabels();
    }}

    // --- Multi-select filter state ---
    const FILTER_IDS = ["f-family", "f-arch", "f-quant", "f-gpu"];
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

    // Build multi-panel checkboxes from a select element's options
    function buildMultiPanel(id) {{
      const sel = document.getElementById(id);
      const panel = document.getElementById("mp-" + id);
      if (!panel) return;
      panel.innerHTML = "";
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
    populateComputed("f-gpu", r => r.dataset.hw_arch || "");

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

    function rowValueForFilter(row, id) {{
      if (id === "f-family") return (row.dataset.model_family || "").toLowerCase();
      if (id === "f-arch") return (row.dataset.architecture || "").toLowerCase();
      if (id === "f-quant") return (row.dataset.quantization || "").toLowerCase();
      if (id === "f-gpu") return (row.dataset.hw_arch || "").toLowerCase();
      return "";
    }}

    // Slider matching helpers
    function rowMatchesParamsSlider(row) {{
      const p = parseParams(row.dataset.params_total || "");
      if (isNaN(p)) return true; // unknown passes through
      const lo = PARAMS_SNAPS[sliderState.paramsMin];
      const hi = PARAMS_SNAPS[sliderState.paramsMax];
      // hi at max index = no upper bound
      return p >= lo && (sliderState.paramsMax === PARAMS_SNAPS.length - 1 || p <= hi);
    }}

    function rowMatchesCtxSlider(row) {{
      const raw = row.dataset.context_length || "";
      if (!raw || raw === "unknown") return true;
      const m = raw.match(/([\\d]+)/);
      if (!m) return true;
      const v = parseInt(m[1]);
      const lo = CTX_SNAPS[sliderState.ctxMin];
      const hi = CTX_SNAPS[sliderState.ctxMax];
      return v >= lo && (sliderState.ctxMax === CTX_SNAPS.length - 1 || v <= hi);
    }}

    function rowMatchesVramSlider(row) {{
      const mb = parseInt(row.dataset.hw_vram_mb || "-1");
      if (mb < 0) return true; // no VRAM data passes through
      const gb = mb / 1024;
      const lo = VRAM_SNAPS[sliderState.vramMin];
      const hi = VRAM_SNAPS[sliderState.vramMax];
      return gb >= lo && (sliderState.vramMax === VRAM_SNAPS.length - 1 || gb < hi);
    }}

    // Multi-dimensional filter with OR within dimension, AND across
    function applyFilters() {{
      let visibleCount = 0;
      rows.forEach(row => {{
        const text = row.textContent.toLowerCase();
        const query = fText.value.toLowerCase();
        const matchText = !query || text.includes(query);

        let match = true;
        FILTER_IDS.forEach(id => {{
          const activeVals = getActiveValues(id);
          if (activeVals.length === 0) return;
          const rowVal = rowValueForFilter(row, id);
          const dimMatch = activeVals.some(v => rowVal === v.toLowerCase());
          if (!dimMatch) match = false;
        }});

        // Slider filters
        if (!rowMatchesParamsSlider(row)) match = false;
        if (!rowMatchesCtxSlider(row)) match = false;
        if (!rowMatchesVramSlider(row)) match = false;

        const visibleRow = matchText && match;
        row.hidden = !visibleRow;
        // Also toggle the adjacent detail row
        const next = row.nextElementSibling;
        if (next && next.classList.contains("row-detail")) {{
          if (!visibleRow) next.style.display = "none";
        }}
        if (visibleRow) visibleCount++;
      }});
      noResults.style.display = visibleCount === 0 ? "block" : "none";
      updateAutoHide();
      renderSimilarResults();
    }}

    // Similar Results column: group visible rows by hw_tier + vram bucket, fill column
    function vramBucket(mb) {{
      if (mb < 0) return -1;
      const gb = mb / 1024;
      if (gb < 8) return 0;
      if (gb < 16) return 1;
      if (gb < 24) return 2;
      if (gb < 40) return 3;
      return 4;
    }}

    function renderSimilarResults() {{
      const groups = {{}};
      rows.forEach(row => {{
        if (row.hidden) return;
        const tier = row.dataset.hw_tier || "unknown";
        const mb = parseInt(row.dataset.hw_vram_mb || "-1");
        const bucket = vramBucket(mb);
        const key = tier + "|" + bucket;
        if (!(key in groups)) groups[key] = [];
        groups[key].push(row);
      }});

      const showSimilar = colVisible[11] !== false;
      rows.forEach(row => {{
        const cell = row.querySelector(".similar-results-col");
        if (!cell) return;
        cell.style.display = showSimilar ? "" : "none";
        const tier = row.dataset.hw_tier || "unknown";
        const mb = parseInt(row.dataset.hw_vram_mb || "-1");
        const bucket = vramBucket(mb);
        const key = tier + "|" + bucket;
        const grp = groups[key] || [];
        if (grp.length > 1) {{
          const badge = document.createElement("span");
          badge.className = "similar-badge";
          badge.textContent = (grp.length - 1) + " similar";
          cell.innerHTML = "";
          cell.appendChild(badge);
        }} else {{
          cell.textContent = "—";
        }}
      }});
      similarColHeader.style.display = showSimilar ? "" : "none";
    }}

    // GPU arch from PCI
    function _gpu_arch_family(hw_str) {{
      if (typeof hw_str !== "string") return "unknown";
      if (hw_str.includes("10de:2204")) return "Ada Lovelace";
      if (hw_str.includes("10de:a000")) return "Ampere";
      if (hw_str.includes("10de:2334")) return "Hopper";
      if (hw_str.includes("10de:20b2")) return "Ampere";
      if (hw_str.includes("H100") || hw_str.includes("H200")) return "Hopper";
      if (hw_str.includes("A100")) return "Ampere";
      return "unknown";
    }}

    // Toggle hardware column — now via checkbox in gear panel
    function setHwVisible(visible) {{
      hwVisible = visible;
      hwCol.style.display = visible ? "table-cell" : "none";
      rows.forEach(r => {{
        const hwTd = r.querySelector(".hw-col-td");
        if (hwTd) hwTd.style.display = visible ? "" : "none";
      }});
      colVisible[12] = visible;
      try {{ localStorage.setItem("hw_visible", visible ? "true" : "false"); }} catch(e) {{}}
      const hwCheck = document.getElementById("toggle-hw-check");
      if (hwCheck) hwCheck.checked = visible;
    }}

    document.getElementById("toggle-hw-check").addEventListener("change", (e) => {{
      setHwVisible(e.target.checked);
    }});
    try {{
      if (localStorage.getItem("hw_visible") === "true") setHwVisible(true);
    }} catch(e) {{}}

    // --- Column visibility panel ---
    const COL_NAMES = [
      "Run ID", "Timestamp", "Signer", "Models", "Judge Mode",
      "Model Family", "Architecture", "Params (total)", "Params (active)",
      "Context Len", "Quantization", "Similar Results"
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
        if (idx === 12) return; // hardware handled by setHwVisible
        if (idx === 11) return; // similar results handled by renderSimilarResults
        const show = colVisible[idx] !== false;
        th.style.display = show ? "" : "none";
      }});
      rows.forEach(row => {{
        const cells = Array.from(row.querySelectorAll("td"));
        cells.forEach((td, idx) => {{
          if (idx === 12) return; // hardware handled separately
          if (idx === 11) return; // similar results handled separately
          td.style.display = colVisible[idx] !== false ? "" : "none";
        }});
      }});
    }}

    function buildColVisPanel() {{
      const list = document.getElementById("col-vis-list");
      list.innerHTML = "";
      // Cols 0-11; hardware (12) handled by separate checkbox below hr
      for (let i = 0; i <= 11; i++) {{
        const lbl = document.createElement("label");
        const cb = document.createElement("input");
        cb.type = "checkbox";
        cb.dataset.colIdx = i;
        cb.checked = colVisible[i] !== false;
        cb.addEventListener("change", () => {{
          colVisible[i] = cb.checked;
          colOverride.add(i);
          saveColState();
          applyColVisibility();
          if (i === 11) renderSimilarResults();
        }});
        lbl.appendChild(cb);
        lbl.appendChild(document.createTextNode(" " + COL_NAMES[i]));
        list.appendChild(lbl);
      }}
    }}

    // Auto-hide rule: single-select filter on a field → auto-hide that column
    function updateAutoHide() {{
      Object.entries(FILTER_TO_COL).forEach(([filterId, colIdx]) => {{
        if (colOverride.has(colIdx)) return;
        const singleActive = (filterMode[filterId] === "single")
          ? (document.getElementById(filterId) && document.getElementById(filterId).value !== "")
          : false;
        colVisible[colIdx] = !singleActive;
      }});
      saveColState();
      applyColVisibility();
      document.querySelectorAll("#col-vis-list input[type=checkbox]").forEach(cb => {{
        const idx = parseInt(cb.dataset.colIdx);
        if (!isNaN(idx)) cb.checked = colVisible[idx] !== false;
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

    // --- Sorting ---
    function getCellValue(row, colIdx) {{
      const cells = row.querySelectorAll("td");
      if (!cells[colIdx]) return "";
      return cells[colIdx].textContent.trim();
    }}

    function compareValues(a, b, colIdx) {{
      if (colIdx === 1) return a.localeCompare(b);
      // Params total (7), params active (8)
      if (colIdx === 7 || colIdx === 8) {{
        const na = parseParams(a), nb = parseParams(b);
        if (!isNaN(na) && !isNaN(nb)) return na - nb;
        if (!isNaN(na)) return -1;
        if (!isNaN(nb)) return 1;
        return a.localeCompare(b);
      }}
      // Context Len (9)
      if (colIdx === 9) {{
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

    // --- Collapsible filter bar ---
    const filterRowsWrap = document.getElementById("filter-rows-wrap");
    const filterChipStrip = document.getElementById("filter-chip-strip");
    const filterToggleBtn = document.getElementById("filter-toggle");

    const FILTER_LABELS = {{
      "f-family": "Model Family",
      "f-arch": "Architecture",
      "f-quant": "Quantization",
      "f-gpu": "GPU Architecture"
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

    // --- Clear All inside filter box ---
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
      // Reset sliders
      document.getElementById("params-min").value = 0;
      document.getElementById("params-max").value = PARAMS_SNAPS.length - 1;
      document.getElementById("ctx-min").value = 0;
      document.getElementById("ctx-max").value = CTX_SNAPS.length - 1;
      document.getElementById("vram-min").value = 0;
      document.getElementById("vram-max").value = VRAM_SNAPS.length - 1;
      sliderState = {{ paramsMin: 0, paramsMax: PARAMS_SNAPS.length - 1, ctxMin: 0, ctxMax: CTX_SNAPS.length - 1, vramMin: 0, vramMax: VRAM_SNAPS.length - 1 }};
      // Re-update labels
      document.getElementById("params-min-lbl").textContent = PARAMS_LABELS[0];
      document.getElementById("params-max-lbl").textContent = PARAMS_LABELS[PARAMS_LABELS.length - 1];
      document.getElementById("ctx-min-lbl").textContent = CTX_LABELS[0];
      document.getElementById("ctx-max-lbl").textContent = CTX_LABELS[CTX_LABELS.length - 1];
      document.getElementById("vram-min-lbl").textContent = VRAM_LABELS[0];
      document.getElementById("vram-max-lbl").textContent = VRAM_LABELS[VRAM_LABELS.length - 1];
      saveSliderState();
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

    // --- Row expand for config hash click-to-copy ---
    rows.forEach(row => {{
      const cfgHash = row.dataset.config_hash;
      if (!cfgHash) return;
      row.style.cursor = "pointer";
      row.title = "Click to show config hash";
      row.addEventListener("click", (e) => {{
        // Don't trigger on copy button itself
        if (e.target.classList.contains("copy-btn")) return;
        const next = row.nextElementSibling;
        if (next && next.classList.contains("row-detail")) {{
          const isHidden = next.style.display === "none" || next.style.display === "";
          next.style.display = isHidden ? "table-row" : "none";
        }}
      }});
    }});

    // Click-to-copy config hash
    document.addEventListener("click", (e) => {{
      if (!e.target.classList.contains("copy-btn")) return;
      const text = e.target.dataset.copy;
      if (!text) return;
      navigator.clipboard.writeText(text).then(() => {{
        const orig = e.target.textContent;
        e.target.textContent = "copied!";
        setTimeout(() => {{ e.target.textContent = orig; }}, 1200);
      }}).catch(() => {{
        e.target.textContent = "failed";
        setTimeout(() => {{ e.target.textContent = "copy"; }}, 1200);
      }});
    }});

    initSliders();
    applyFilters();
    renderSimilarResults();
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
