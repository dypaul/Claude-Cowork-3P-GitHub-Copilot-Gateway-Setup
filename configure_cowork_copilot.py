#!/usr/bin/env python3
"""
Configure Claude Cowork 3P to use GitHub Copilot as inference gateway.

Discovers available Claude models from the GitHub Copilot API,
writes the Cowork 3P config (configLibrary) and optionally
the Claude Code settings (~/.claude/settings.json).

Usage:
    python3 configure_cowork_copilot.py
    python3 configure_cowork_copilot.py --token gho_xxxxx
    python3 configure_cowork_copilot.py --token gho_xxxxx --skip-code-tab
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import urllib.request
import urllib.error
import uuid
from pathlib import Path
from typing import Optional

GATEWAY_URL = "https://api.githubcopilot.com"
MODELS_ENDPOINT = f"{GATEWAY_URL}/models"

COWORK_INCOMPATIBLE = {
    "claude-opus-4.7",
}


# ── Paths ────────────────────────────────────────────────────────────────────

def get_cowork_3p_dir() -> Path:
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "Claude-3p"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")
        return Path(appdata) / "Claude-3p"
    else:
        sys.exit(f"Unsupported platform: {system}")


def get_claude_code_settings_path() -> Path:
    return Path.home() / ".claude" / "settings.json"


# ── GitHub Copilot model discovery ───────────────────────────────────────────

def fetch_models(token: str) -> list[dict]:
    """Fetch all models from GitHub Copilot that support /v1/messages."""
    req = urllib.request.Request(
        MODELS_ENDPOINT,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        sys.exit(f"GitHub Copilot API error {e.code}: {e.read().decode()[:200]}")
    except urllib.error.URLError as e:
        sys.exit(f"Network error: {e.reason}")

    models = []
    for m in data.get("data", []):
        endpoints = m.get("supported_endpoints", [])
        if "/v1/messages" in endpoints:
            models.append({
                "id": m["id"],
                "name": m.get("name", m["id"]),
                "family": m.get("capabilities", {}).get("family", ""),
                "max_ctx": m.get("capabilities", {}).get("limits", {}).get(
                    "max_context_window_tokens", 0
                ),
            })
    return models


def pick_one_per_family(models: list[dict]) -> list[dict]:
    """Keep only the latest model per family (highest version)."""
    families: dict[str, dict] = {}
    for m in models:
        base = m["family"].rsplit("-", 1)[0] if "-" in m["family"] else m["family"]
        # 1m variants get their own slot
        if "1m" in m["id"]:
            base += "-1m"
        if base not in families or m["id"] > families[base]["id"]:
            families[base] = m
    return sorted(families.values(), key=lambda m: m["id"], reverse=True)


# ── Config writers ───────────────────────────────────────────────────────────

def build_inference_models(models: list[dict], all_models: bool) -> str:
    """Build the inferenceModels JSON string value."""
    entries = []
    for m in models:
        if "1m" in m["id"]:
            entries.append({"name": m["id"], "supports1m": True})
        else:
            entries.append(m["id"])
    return json.dumps(entries, separators=(",", ":"))


PROFILE_NAME = "GitHub Copilot"


def ensure_developer_mode():
    dev_settings_path = get_cowork_3p_dir() / "developer_settings.json"
    dev_settings_path.parent.mkdir(parents=True, exist_ok=True)

    if dev_settings_path.exists():
        settings = json.loads(dev_settings_path.read_text())
        if settings.get("allowDevTools"):
            return False
        settings["allowDevTools"] = True
    else:
        settings = {"allowDevTools": True}

    dev_settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return True


def write_cowork_config(token: str, inference_models_str: str) -> Path:
    """Write or update the Cowork 3P configLibrary entry."""
    config_lib = get_cowork_3p_dir() / "configLibrary"
    config_lib.mkdir(parents=True, exist_ok=True)

    meta_path = config_lib / "_meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"entries": []}
    entries = meta.get("entries", [])

    existing = next(
        (e for e in entries if e["name"].lower() == PROFILE_NAME.lower()), None
    )

    if existing:
        config_id = existing["id"]
    else:
        config_id = str(uuid.uuid4())
        entries.append({"id": config_id, "name": PROFILE_NAME})

    meta["appliedId"] = config_id
    meta["entries"] = entries
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    config_file = config_lib / f"{config_id}.json"
    config = json.loads(config_file.read_text()) if config_file.exists() else {}

    config["inferenceProvider"] = "gateway"
    config["inferenceGatewayBaseUrl"] = GATEWAY_URL
    config["inferenceGatewayApiKey"] = token
    config["inferenceModels"] = inference_models_str

    config_file.write_text(json.dumps(config, indent=2) + "\n")
    config_file.chmod(0o600)
    return config_file


def write_code_tab_settings(models: list[dict]) -> Optional[Path]:
    """Update ~/.claude/settings.json with modelOverrides and env vars."""
    settings_path = get_claude_code_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)

    if settings_path.exists():
        settings = json.loads(settings_path.read_text())
    else:
        settings = {}

    # Build modelOverrides: anthropic ID (hyphens) → copilot ID (dots)
    overrides = {}
    for m in models:
        anthropic_id = m["id"].replace(".", "-")
        overrides[anthropic_id] = m["id"]
    settings["modelOverrides"] = overrides

    # Build env vars for display names
    env = settings.get("env", {})
    family_map = {
        "opus": None,
        "sonnet": None,
        "haiku": None,
    }
    for m in models:
        if "1m" in m["id"]:
            continue
        for family in family_map:
            if family in m["id"] and family_map[family] is None:
                family_map[family] = m
                break

    for family, model in family_map.items():
        if model is None:
            continue
        key = family.upper()
        env[f"ANTHROPIC_DEFAULT_{key}_MODEL"] = model["id"]
        env[f"ANTHROPIC_DEFAULT_{key}_MODEL_NAME"] = model["name"]
        env[f"ANTHROPIC_DEFAULT_{key}_MODEL_DESCRIPTION"] = (
            f"{model['name']} via GitHub Copilot"
        )

    settings["env"] = env
    settings_path.write_text(json.dumps(settings, indent=2) + "\n")
    return settings_path


# ── CLI ──────────────────────────────────────────────────────────────────────

def get_token_from_gh_cli() -> Optional[str]:
    """Try to get token from `gh auth token`."""
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def prompt_token() -> str:
    print("GitHub Copilot OAuth token (gho_...):")
    print("  Get it with: gh auth token")
    token = input("> ").strip()
    if not token:
        sys.exit("No token provided.")
    return token


def main():
    parser = argparse.ArgumentParser(
        description="Configure Claude Cowork 3P with GitHub Copilot gateway"
    )
    parser.add_argument("--token", help="GitHub Copilot OAuth token. Prefer env var GITHUB_TOKEN or gh CLI instead (--token is visible in shell history)")
    parser.add_argument(
        "--all-models",
        action="store_true",
        help="Include all model versions (not just one per family)",
    )
    parser.add_argument(
        "--skip-code-tab",
        action="store_true",
        help="Skip configuring Claude Code tab (~/.claude/settings.json)",
    )
    args = parser.parse_args()

    token = args.token or os.environ.get("GITHUB_TOKEN") or get_token_from_gh_cli()
    if token and not args.token:
        source = "env GITHUB_TOKEN" if os.environ.get("GITHUB_TOKEN") else "gh CLI"
        print(f"Token auto-detected from {source}: {token[:4]}****")
    if not token:
        token = prompt_token()

    # 1. Discover models
    print(f"\nQuerying {MODELS_ENDPOINT} ...")
    all_models = fetch_models(token)
    if not all_models:
        sys.exit("No models with /v1/messages support found.")

    print(f"Found {len(all_models)} Claude models supporting /v1/messages:\n")
    for m in all_models:
        ctx = f"{m['max_ctx'] // 1000}K" if m["max_ctx"] else "?"
        flag = "  ⚠ incompatible" if m["id"] in COWORK_INCOMPATIBLE else ""
        print(f"  {m['id']:30s}  {m['name']:45s}  {ctx}{flag}")

    incompatible = [m for m in all_models if m["id"] in COWORK_INCOMPATIBLE]
    if incompatible:
        print(f"\n⚠  {len(incompatible)} model(s) excluded (require thinking.type=adaptive, unsupported by Cowork):")
        for m in incompatible:
            print(f"     {m['id']}  — see https://github.com/anthropics/claude-code/issues/52541")
    compatible = [m for m in all_models if m["id"] not in COWORK_INCOMPATIBLE]

    # 2. Pick models
    if args.all_models:
        selected = compatible
        print(f"\n--all-models: keeping all {len(selected)} compatible models.")
        print("  ⚠  Warning: Cowork picker shows truncated names (e.g. 'Opus 4')")
        print("     Models from the same family will be indistinguishable.\n")
    else:
        selected = pick_one_per_family(compatible)
        skipped = [m for m in compatible if m not in selected]
        print(f"\nSelected {len(selected)} models (one per family):\n")
        for m in selected:
            print(f"  {m['id']:30s}  {m['name']}")
        if skipped:
            print(f"\nSkipped {len(skipped)} models (same family, use --all-models to include):\n")
            for m in skipped:
                print(f"  {m['id']:30s}  {m['name']}")

    # 3. Enable Developer Mode
    if ensure_developer_mode():
        print("\n✅ Developer Mode enabled (developer_settings.json)")
    else:
        print("\n✔  Developer Mode already enabled")

    # 4. Write Cowork config
    inference_str = build_inference_models(selected, args.all_models)
    cowork_path = write_cowork_config(token, inference_str)
    print(f"✅ Cowork 3P config written: {cowork_path}")

    # 5. Write Code tab settings
    if not args.skip_code_tab:
        code_path = write_code_tab_settings(selected)
        print(f"✅ Claude Code settings written: {code_path}")
    else:
        print("⏭  Skipped Claude Code tab config.")

    # 5. Summary
    print("\n" + "─" * 60)
    print("Done. Restart Claude Desktop (Cmd+Q then relaunch).")
    print()
    print("Cowork tab → model picker will show the configured models.")
    print("Code tab   → /model picker will show display names.")
    print("─" * 60)


if __name__ == "__main__":
    main()
