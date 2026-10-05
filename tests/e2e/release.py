"""Pinned HACS 2.0.5 API release-install gate; requires human-authorized test seed."""

import hashlib
import json
from pathlib import Path

from ha_testbed.api import wait_until


def install_release(cfg, run, credentials, ha):
    repo = "brennand97/voice-satellite-card-integration"
    if not run["online"] or run["hacs"] != "authorized-seed":
        raise RuntimeError(
            "HACS release installs require online mode and a private authorized seed"
        )
    wait_until(
        lambda: any(
            e["domain"] == "hacs" and e["state"] == "loaded"
            for e in ha.request("/api/config/config_entries/entry")
        )
    )
    ha.ws(
        {
            "type": "hacs/repositories/add",
            "repository": "https://github.com/" + repo,
            "category": "integration",
        },
        timeout=120,
    )
    repositories = ha.ws(
        {"type": "hacs/repositories/list", "categories": ["integration"]}
    )
    candidate = next((r for r in repositories if r["full_name"] == repo), None)
    if not candidate:
        raise RuntimeError("HACS did not register the candidate repository")
    repository_id = str(candidate["id"])
    ha.ws(
        {
            "type": "hacs/repository/download",
            "repository": repository_id,
            "version": run["release"],
        },
        timeout=180,
    )
    installed = next(
        r for r in ha.ws({"type": "hacs/repositories/list"}) if r["full_name"] == repo
    )
    if not installed["installed"] or installed["installed_version"].removeprefix(
        "v"
    ) != run["release"].removeprefix("v"):
        raise RuntimeError("HACS installed version does not match requested release")
    component = Path(run["directory"]) / "config/custom_components/voice_satellite"
    if not (component / "frontend/voice-satellite-panel.js").exists():
        raise RuntimeError("HACS release missing built frontend")
    digest = hashlib.sha256()
    for file in sorted(component.rglob("*")):
        if file.is_file() and "__pycache__" not in file.parts:
            digest.update(str(file.relative_to(component)).encode())
            digest.update(file.read_bytes())
    run["candidate_sha256"] = digest.hexdigest()
    run["installed_version"] = json.loads((component / "manifest.json").read_text())[
        "version"
    ]
    run["release_install"] = "hacs-verified"
