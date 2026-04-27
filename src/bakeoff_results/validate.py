"""Validate packaged bakeoff result bundles.

The validator intentionally performs structural and integrity checks only. It
expects Sigstore/Rekor verification to happen in CI with the Sigstore tooling
once the submitting workflow and identity policy are finalized.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = "bakeoff-results/v1"
REQUIRED_BUNDLE_FILES = ("result.json", "manifest.json", "summary.md")
OPTIONAL_BUNDLE_FILES = ("dashboard.html", "signature.sigstore.json")
HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class BundleValidationError(ValueError):
    """Raised when a bundle fails validation."""


@dataclass(frozen=True)
class ValidatedBundle:
    """Loaded data for a bundle that passed validation."""

    path: Path
    manifest: dict[str, Any]
    result: dict[str, Any]


def load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BundleValidationError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise BundleValidationError(f"{path.name} must contain a JSON object")
    return data


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_version(manifest: dict[str, Any]) -> str | None:
    value = manifest.get("schema_version", manifest.get("schemaVersion"))
    return str(value) if value is not None else None


def _manifest_file_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    files = manifest.get("files")
    if isinstance(files, dict):
        hashes: dict[str, str] = {}
        for file_path, entry in files.items():
            if isinstance(entry, str):
                hashes[str(file_path)] = entry
            elif isinstance(entry, dict) and isinstance(entry.get("sha256"), str):
                hashes[str(file_path)] = entry["sha256"]
            else:
                raise BundleValidationError(
                    f"manifest files entry for {file_path!r} must provide a sha256"
                )
        return hashes

    if isinstance(files, list):
        hashes = {}
        for entry in files:
            if not isinstance(entry, dict):
                raise BundleValidationError("manifest files list entries must be objects")
            file_path = entry.get("path")
            digest = entry.get("sha256")
            if not isinstance(file_path, str) or not isinstance(digest, str):
                raise BundleValidationError(
                    "manifest files list entries require path and sha256 strings"
                )
            hashes[file_path] = digest
        return hashes

    raise BundleValidationError("manifest must include files as an object or list")


def _safe_bundle_path(bundle_dir: Path, relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute() or ".." in path.parts:
        raise BundleValidationError(f"manifest file path is unsafe: {relative_path}")
    return bundle_dir / path


def _require_string(data: dict[str, Any], field: str, owner: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BundleValidationError(f"{owner} requires non-empty string field {field!r}")
    return value


def _model_ids(result: dict[str, Any]) -> list[str]:
    model_ids = result.get("model_ids")
    if isinstance(model_ids, list) and all(isinstance(item, str) and item for item in model_ids):
        return model_ids

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
            if not isinstance(model, dict):
                raise BundleValidationError("result models entries must be objects")
            model_id = model.get("id", model.get("model_id"))
            if not isinstance(model_id, str) or not model_id.strip():
                raise BundleValidationError("result models entries require id or model_id")
            extracted.append(model_id)
        if extracted:
            return extracted

    raise BundleValidationError("result requires non-empty model_ids or models")


def validate_result(result: dict[str, Any]) -> None:
    _require_string(result, "run_id", "result")
    _require_string(result, "timestamp", "result")

    provenance = result.get("provenance")
    if not isinstance(provenance, dict):
        raise BundleValidationError("result requires provenance object")
    has_repository = any(
        isinstance(provenance.get(field), str) and provenance.get(field)
        for field in ("source_repository", "repository")
    )
    # Current bakeoff payloads capture the commit under provenance.git.sha.
    # Repository identity is implied by the result bundle source and signer.
    git = provenance.get("git")
    has_git_sha = isinstance(git, dict) and isinstance(git.get("sha"), str) and bool(git["sha"])
    if not has_repository and not has_git_sha:
        raise BundleValidationError(
            "result provenance requires source_repository, repository, or git.sha"
        )
    if not has_git_sha and not any(
        isinstance(provenance.get(field), str) and provenance.get(field)
        for field in ("source_commit", "commit")
    ):
        raise BundleValidationError("result provenance requires source_commit, commit, or git.sha")

    _model_ids(result)


def validate_signer_metadata(manifest: dict[str, Any], bundle_dir: Path) -> None:
    signer = manifest.get("signer")
    if signer is None:
        return
    if not isinstance(signer, dict):
        raise BundleValidationError("manifest signer must be an object when present")

    has_identity = any(
        isinstance(signer.get(field), str) and signer.get(field)
        for field in ("identity", "subject", "repository")
    )
    if not has_identity:
        raise BundleValidationError(
            "manifest signer requires identity, subject, or repository"
        )

    issuer = signer.get("issuer")
    if issuer is not None and (not isinstance(issuer, str) or not issuer.strip()):
        raise BundleValidationError("manifest signer issuer must be a non-empty string")

    policy = signer.get("policy")
    if policy is not None and not isinstance(policy, (str, dict)):
        raise BundleValidationError("manifest signer policy must be a string or object")

    signature_path = bundle_dir / "signature.sigstore.json"
    if signature_path.exists():
        signature = load_json(signature_path)
        has_rekor_evidence = any(
            key in signature
            for key in ("rekor", "transparencyLogEntries", "verificationMaterial")
        )
        if not has_rekor_evidence:
            raise BundleValidationError(
                "signature.sigstore.json must include Rekor or verification material"
            )


def validate_bundle(bundle_dir: Path | str) -> ValidatedBundle:
    bundle_path = Path(bundle_dir)
    if not bundle_path.is_dir():
        raise BundleValidationError(f"bundle path is not a directory: {bundle_path}")

    for relative_path in REQUIRED_BUNDLE_FILES:
        if not (bundle_path / relative_path).is_file():
            raise BundleValidationError(f"bundle missing required file: {relative_path}")

    manifest = load_json(bundle_path / "manifest.json")
    if _schema_version(manifest) != SCHEMA_VERSION:
        raise BundleValidationError(
            f"manifest schema_version must be {SCHEMA_VERSION!r}"
        )

    manifest_hashes = _manifest_file_hashes(manifest)
    files_requiring_hash = [
        path
        for path in (*REQUIRED_BUNDLE_FILES, *OPTIONAL_BUNDLE_FILES)
        if path != "manifest.json" and (bundle_path / path).exists()
    ]
    for relative_path in files_requiring_hash:
        expected = manifest_hashes.get(relative_path)
        if expected is None:
            raise BundleValidationError(
                f"manifest missing sha256 for bundle file: {relative_path}"
            )
        if not HEX_SHA256.fullmatch(expected):
            raise BundleValidationError(f"sha256 for {relative_path} is not valid hex")
        actual = sha256_file(bundle_path / relative_path)
        if actual.lower() != expected.lower():
            raise BundleValidationError(
                f"sha256 mismatch for {relative_path}: expected {expected}, got {actual}"
            )

    for relative_path, expected in manifest_hashes.items():
        if relative_path == "manifest.json":
            continue
        file_path = _safe_bundle_path(bundle_path, relative_path)
        if not file_path.is_file():
            raise BundleValidationError(f"manifest references missing file: {relative_path}")
        if not HEX_SHA256.fullmatch(expected):
            raise BundleValidationError(f"sha256 for {relative_path} is not valid hex")
        actual = sha256_file(file_path)
        if actual.lower() != expected.lower():
            raise BundleValidationError(
                f"sha256 mismatch for {relative_path}: expected {expected}, got {actual}"
            )

    result = load_json(bundle_path / "result.json")
    validate_result(result)

    bundle_info = manifest.get("bundle")
    if isinstance(bundle_info, dict):
        if bundle_info.get("run_id") not in (None, result.get("run_id")):
            raise BundleValidationError("manifest bundle.run_id does not match result.run_id")
        if bundle_info.get("timestamp") not in (None, result.get("timestamp")):
            raise BundleValidationError(
                "manifest bundle.timestamp does not match result.timestamp"
            )

    validate_signer_metadata(manifest, bundle_path)
    return ValidatedBundle(path=bundle_path, manifest=manifest, result=result)


def discover_bundles(paths: Iterable[Path]) -> list[Path]:
    bundles: list[Path] = []
    for path in paths:
        if path.is_file() and path.name == "manifest.json":
            bundles.append(path.parent)
        elif path.is_dir():
            if (path / "manifest.json").is_file():
                bundles.append(path)
            bundles.extend(manifest.parent for manifest in path.glob("**/manifest.json"))
    return sorted(set(bundles))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="Bundle dirs or scan roots")
    parser.add_argument(
        "--scan",
        action="store_true",
        help="Treat paths as roots and validate every nested manifest.json",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Exit successfully when --scan finds no bundles",
    )
    args = parser.parse_args(argv)

    targets = discover_bundles(args.paths) if args.scan else args.paths
    if not targets:
        if args.allow_empty:
            print("No bundles found.")
            return 0
        print("No bundles found.", file=sys.stderr)
        return 1

    for target in targets:
        validate_bundle(target)
        print(f"OK {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
