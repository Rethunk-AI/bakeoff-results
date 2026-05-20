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
    .hw-badge {{ display: inline-block; margin-left: 0.5rem; font-size: 0.8em; color: #58a6ff; background: #ddf4ff; padding: 1px 6px; border-radius: 3px; }}
    .hw-col {{ display: table-cell; }}
    .toggle-btn {{ padding: 0.4rem 0.8rem; background: #f6f8fa; border: 1px solid #ddd; border-radius: 4px; cursor: pointer; font-size: 0.85em; }}
    .toggle-btn:hover {{ background: #e8eaed; }}
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
        <div class="filter-group">
          <label for="f-family">Model Family</label>
          <select id="f-family"><option value="">All</option></select>
        </div>
      <div class="filter-group">
        <label for="f-arch">Architecture</label>
        <select id="f-arch"><option value="">All</option></select>
      </div>
      <div class="filter-group">
        <label for="f-quant">Quantization</label>
        <select id="f-quant"><option value="">All</option></select>
      </div>
      <div class="filter-group">
        <label for="f-ctx">Context Length</label>
        <select id="f-ctx"><option value="">All</option></select>
      </div>
    </div>
    <div class="filter-row">
      <div class="filter-group">
        <label for="f-params">Parameter Range</label>
        <select id="f-params"><option value="">Any</option></select>
      </div>
      <div class="filter-group">
        <label for="f-gpu">GPU Architecture</label>
        <select id="f-gpu"><option value="">All</option></select>
      </div>
      <div class="filter-group">
        <label for="f-vram">VRAM Tier</label>
        <select id="f-vram"><option value="">All</option></select>
      </div>
    </div>
    </div>
  </div>
  <button class="toggle-btn" id="toggle-hw">Show Hardware</button>
  <label for="f-text">Quick search</label>
  <input id="f-text" type="search" placeholder="Filter by run, signer, model, judge mode, config hash, or hardware">
  <table>
    <thead>
      <tr>
        <th>Run ID</th>
        <th>Timestamp</th>
        <th>Signer</th>
        <th>Models</th>
        <th>Judge Mode</th>
        <th>Config Hash</th>
        <th>Model Family</th>
        <th>Architecture</th>
        <th>Params (total)</th>
        <th>Params (active)</th>
        <th>Context Len</th>
        <th>Quantization</th>
        <th class="hw-col" id="hw-col-header" style="display:none">Hardware</th>
      </tr>
    </thead>
    <tbody id="results">
{body_rows}
    </tbody>
  </table>
  <p id="no-results" style="display:none">No results match selected filters.</p>
  <script>
    const fText = document.getElementById("f-text");
    const rows = Array.from(document.querySelectorAll("#results tr"));
    const hwCol = document.getElementById("hw-col-header");
    const noResults = document.getElementById("no-results");
    let hwVisible = false;

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

    // Populate all filter dropdowns
    populateSelect("f-family", "model_family");
    populateSelect("f-arch", "architecture");
    populateSelect("f-quant", "quantization");
    populateComputed("f-ctx",    r => ctxRange(r.dataset.context_length || ""));
    populateComputed("f-params", r => paramsRange(parseParams(r.dataset.params_total || "")));
    populateComputed("f-gpu",    r => r.dataset.hw_arch || "");
    populateComputed("f-vram",   r => r.dataset.hw_bucket || "");

    // Multi-dimensional filter
    function applyFilters() {{
      let visible = 0;
      rows.forEach(row => {{
        const text = row.textContent.toLowerCase();
        const query = fText.value.toLowerCase();
        const matchText = !query || text.includes(query);

        let match = true;
        const family = document.getElementById("f-family").value;
        if (family && (row.dataset.model_family || "").toLowerCase() !== family.toLowerCase()) match = false;

        const arch = document.getElementById("f-arch").value;
        if (arch && (row.dataset.architecture || "").toLowerCase() !== arch.toLowerCase()) match = false;

        const quant = document.getElementById("f-quant").value;
        if (quant && (row.dataset.quantization || "").toLowerCase() !== quant.toLowerCase()) match = false;

        const ctx = document.getElementById("f-ctx").value;
        if (ctx) {{
          const rowCtx = ctxRange(row.dataset.context_length || "");
          if (rowCtx !== "unknown" && rowCtx !== ctx) match = false;
        }}

        const params = document.getElementById("f-params").value;
        if (params) {{
          const p = parseParams(row.dataset.params_total || "0");
          const r = paramsRange(p);
          if (r !== "unknown" && r !== params) match = false;
        }}

        const gpu = document.getElementById("f-gpu").value;
        if (gpu) {{
          const rowArch = row.dataset.hw_arch || "unknown";
          if (rowArch !== "unknown" && rowArch.toLowerCase() !== gpu.toLowerCase()) match = false;
        }}

        const vram = document.getElementById("f-vram").value;
        if (vram) {{
          const rowVram = row.dataset.hw_bucket || "unknown";
          if (rowVram !== "unknown" && rowVram !== vram) match = false;
        }}

        const visible_row = matchText && match;
        row.hidden = !visible_row;
        if (visible_row) visible++;
      }});
      noResults.style.display = visible === 0 ? "block" : "none";
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
      try {{ localStorage.setItem("hw_visible", hwVisible); }} catch(e) {{}}
    }});
    try {{
      if (localStorage.getItem("hw_visible") === "true") {{
        document.getElementById("toggle-hw").click();
      }}
    }} catch(e) {{}}

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
      Object.keys(FILTER_LABELS).forEach(id => {{
        const sel = document.getElementById(id);
        if (!sel || !sel.value) return;
        const chip = document.createElement("span");
        chip.className = "filter-chip";
        const label = document.createElement("span");
        label.textContent = FILTER_LABELS[id] + ": ";
        const val = document.createElement("strong");
        val.textContent = sel.options[sel.selectedIndex].text;
        chip.appendChild(label);
        chip.appendChild(val);
        const clearBtn = document.createElement("button");
        clearBtn.className = "filter-chip-clear";
        clearBtn.textContent = "×";
        clearBtn.title = "Clear " + FILTER_LABELS[id];
        clearBtn.addEventListener("click", () => {{
          sel.value = "";
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

    // Bind all filter inputs
    ["f-family", "f-arch", "f-quant", "f-ctx", "f-params", "f-gpu", "f-vram", "f-text"].forEach(id => {{
      const el = document.getElementById(id);
      el.addEventListener("input", () => {{ applyFilters(); renderChips(); }});
      el.addEventListener("change", () => {{ applyFilters(); renderChips(); }});
    }});

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
