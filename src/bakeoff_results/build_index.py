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


def index_entry(bundle: ValidatedBundle) -> dict[str, Any]:
    result = bundle.result
    return {
        "run_id": result["run_id"],
        "timestamp": result["timestamp"],
        "signer": _signer(bundle.manifest),
        "model_ids": _model_ids(result),
        "judge_mode": result.get("judge_mode")
        or _nested_string(result, "judge", "mode")
        or _nested_string(result, "config", "judge", "mode"),
        "config_hash": result.get("config_hash")
        or _nested_string(result, "provenance", "config_hash")
        or _nested_string(result, "config", "hash")
        or _nested_string(result, "config", "sha256"),
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


def render_html(payload: dict[str, Any]) -> str:
    rows = []
    for entry in payload["entries"]:
        model_ids = ", ".join(entry.get("model_ids") or [])
        cells = [
            entry.get("run_id"),
            entry.get("timestamp"),
            entry.get("signer") or "",
            model_ids,
            entry.get("judge_mode") or "",
            entry.get("config_hash") or "",
        ]
        rows.append(
            "<tr>"
            + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in cells)
            + "</tr>"
        )

    generated_at = html.escape(str(payload["generated_at"]))
    body_rows = "\n".join(rows) if rows else (
        '<tr><td colspan="6">No submissions have been published yet.</td></tr>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Rethunk Bakeoff Results</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 2rem; line-height: 1.5; }}
    input {{ margin: 1rem 0; max-width: 32rem; padding: 0.5rem; width: 100%; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th, td {{ border-bottom: 1px solid #ddd; padding: 0.5rem; text-align: left; }}
    th {{ background: #f6f8fa; }}
  </style>
</head>
<body>
  <h1>Rethunk Bakeoff Results</h1>
  <p>Generated at {generated_at}. This static index is backed by validated
  result bundles and is private until publication is approved.</p>
  <label for="filter">Filter results</label>
  <input id="filter" type="search" placeholder="Filter by run, signer, model, judge mode, or config hash">
  <table>
    <thead>
      <tr>
        <th>Run ID</th>
        <th>Timestamp</th>
        <th>Signer</th>
        <th>Models</th>
        <th>Judge Mode</th>
        <th>Config Hash</th>
      </tr>
    </thead>
    <tbody id="results">
{body_rows}
    </tbody>
  </table>
  <script>
    const filter = document.getElementById("filter");
    const rows = Array.from(document.querySelectorAll("#results tr"));
    filter.addEventListener("input", () => {{
      const query = filter.value.toLowerCase();
      for (const row of rows) {{
        row.hidden = !row.textContent.toLowerCase().includes(query);
      }}
    }});
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
