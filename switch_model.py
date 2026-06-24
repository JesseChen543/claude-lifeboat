#!/usr/bin/env python3
"""
Switch Claude Code between Claude Pro (built-in auth) and any Anthropic-compatible backend.

Provider definitions live in backends.json (next to this script).
API keys are stored in ~/.claude/.api_keys.json (git-ignored).
Settings are written to .vscode/settings.json in the project directory.

Usage:
    switch-model                       Toggle between providers (project-level)
    switch-model setup [provider]      Store a provider's API key (one-time)
    switch-model status                Show current provider for this project
    switch-model claude                Switch this project back to Claude Pro
    switch-model <provider>            Switch this project to a named provider
    switch-model --project <path>      Target a specific project directory
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

KEYS_FILE = Path.home() / ".claude" / ".api_keys.json"
BACKENDS_FILE = Path(__file__).parent / "backends.json"
LOG_FILE = Path.home() / ".claude" / "switch_model.log"

BACKEND_VARS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL")


# ── Logging ───────────────────────────────────────────────────────────

def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except Exception:
        pass


# ── VSCode window reload ──────────────────────────────────────────────



# ── Workspace settings helpers ────────────────────────────────────────

def _write_workspace_settings(project_path: Path, env_vars: dict | None) -> None:
    """Write or remove claudeCode.environmentVariables in <project>/.vscode/settings.json.

    env_vars = {"ANTHROPIC_AUTH_TOKEN": "sk-...", ...} → set custom endpoint
    env_vars = None → remove the block (switch back to Claude Pro)
    """
    settings_path = project_path / ".vscode" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    data = load_json(settings_path)

    if env_vars is not None:
        data["claudeCode.environmentVariables"] = [
            {"name": k, "value": v} for k, v in env_vars.items()
        ]
        data["claudeCode.disableLoginPrompt"] = True
        log(f"workspace settings: wrote {list(env_vars)} to {settings_path}")
    else:
        data.pop("claudeCode.environmentVariables", None)
        data.pop("claudeCode.disableLoginPrompt", None)
        log(f"workspace settings: removed from {settings_path}")

    save_json(settings_path, data)


# ── Helpers ───────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def load_backends() -> dict:
    data = load_json(BACKENDS_FILE)
    providers = data.get("providers", {})
    if not providers:
        print(f"No providers defined in {BACKENDS_FILE}")
        sys.exit(1)
    return providers


def _active_provider(backends: dict, project_path: Path) -> str:
    settings = load_json(project_path / ".vscode" / "settings.json")
    env_list = settings.get("claudeCode.environmentVariables", [])
    env_map = {e["name"]: e["value"] for e in env_list if "name" in e}
    base_url = env_map.get("ANTHROPIC_BASE_URL", "").strip().rstrip("/")
    auth_token = env_map.get("ANTHROPIC_AUTH_TOKEN", "")
    if auth_token and base_url:
        for name, cfg in backends.items():
            if cfg.get("base_url", "").strip().rstrip("/") == base_url:
                return name
    return "claude"


# ── Commands ──────────────────────────────────────────────────────────

def cmd_setup(provider: str | None = None):
    backends = load_backends()

    if provider is None:
        if len(backends) == 1:
            provider = next(iter(backends))
        else:
            names = ", ".join(backends)
            provider = input(f"Provider [{names}]: ").strip().lower()

    if provider not in backends:
        print(f"Unknown provider: {provider}. Available: {', '.join(backends)}")
        sys.exit(1)

    keys = load_json(KEYS_FILE)

    def mask(s: str) -> str:
        return s[:8] + "..." + s[-4:] if len(s) > 14 else "***"

    current = keys.get(provider, "")
    label = backends[provider]["label"]
    prompt = f"{label} API key"
    if current:
        prompt += f" [{mask(current)}]"
    prompt += ": "

    new_key = input(prompt).strip()
    if new_key:
        keys[provider] = new_key

    save_json(KEYS_FILE, keys)
    status = "OK set" if keys.get(provider) else "MISSING"
    print(f"\nKeys saved → {KEYS_FILE}  ({status})")
    print("This file is git-ignored — do not commit it.\n")

    if keys.get(provider):
        choice = input(f"Switch to {provider} now? [y/N]: ").strip().lower()
        if choice in ("y", "yes"):
            project_path = Path.cwd()
            cmd_switch(provider, backends, project_path)


def cmd_toggle(project_path: Path):
    backends = load_backends()
    active = _active_provider(backends, project_path)
    if active == "claude":
        provider = next(iter(backends))
        cmd_switch(provider, backends, project_path)
    else:
        cmd_claude(project_path)


def cmd_switch(provider: str, backends: dict, project_path: Path):
    if _active_provider(backends, project_path) == provider:
        print(f"Already using {backends[provider]['label']} — nothing to change.")
        return

    cfg = backends[provider]
    keys = load_json(KEYS_FILE)
    api_key = keys.get(provider)
    if not api_key:
        print(f"No API key stored for {provider}. Run: switch-model setup {provider}")
        sys.exit(1)

    _write_workspace_settings(project_path, {
        "ANTHROPIC_AUTH_TOKEN": api_key,
        "ANTHROPIC_BASE_URL": cfg["base_url"],
        "ANTHROPIC_MODEL": cfg["model"],
    })
    log(f"switch → {provider}: model={cfg['model']} endpoint={cfg['base_url']} project={project_path}")
    print(f"OK  Now using: {cfg['label']}")
    print(f"  Model:    {cfg['model']}")
    print(f"  Endpoint: {cfg['base_url']}")
    print(f"  Project:  {project_path}")
    print("  Run 'Reload Window' in VSCode (Ctrl+Shift+P) to apply.")


def cmd_claude(project_path: Path):
    settings = load_json(project_path / ".vscode" / "settings.json")
    if "claudeCode.environmentVariables" not in settings:
        print("Already using Claude Pro — nothing to change.")
        return
    _write_workspace_settings(project_path, None)
    log(f"switch → claude: removed workspace settings at {project_path}")
    print("OK  Now using: Claude Pro (built-in auth)")
    print(f"  Project: {project_path}")
    print("  Run 'Reload Window' in VSCode (Ctrl+Shift+P) to apply.")


def cmd_status(project_path: Path):
    backends = load_backends()
    settings = load_json(project_path / ".vscode" / "settings.json")
    env_list = settings.get("claudeCode.environmentVariables", [])
    env_map = {e["name"]: e["value"] for e in env_list if "name" in e}
    active = _active_provider(backends, project_path)

    if active == "claude":
        print("Provider: Claude Pro (built-in auth)")
    else:
        cfg = backends[active]
        print(f"Provider: {cfg['label']}")
        print(f"Model:    {env_map.get('ANTHROPIC_MODEL', cfg['model'])}")
        print(f"Endpoint: {env_map.get('ANTHROPIC_BASE_URL', cfg['base_url'])}")
    print(f"Scope:    {project_path / '.vscode' / 'settings.json'}")

    keys = load_json(KEYS_FILE)
    for name, cfg in backends.items():
        stored = "OK set" if keys.get(name) else f"MISSING (run: switch-model setup {name})"
        print(f"{cfg['label']} key: {stored}")


# ── Main ──────────────────────────────────────────────────────────────

USAGE = """Usage:
    switch-model                       Toggle between providers (project-level)
    switch-model setup [provider]      Store a provider's API key (one-time)
    switch-model status                Show current provider for this project
    switch-model claude                Switch this project back to Claude Pro
    switch-model <provider>            Switch this project to named provider
    switch-model --project <path>      Target a specific project directory"""


def main():
    args = sys.argv[1:]

    project_arg = None
    positional = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--project" and i + 1 < len(args):
            project_arg = args[i + 1]
            i += 2
        elif a in ("--yes", "-y", "--no-restart"):
            i += 1  # silently ignored — kept for backward compat
        else:
            positional.append(a)
            i += 1

    project_path = Path(project_arg).resolve() if project_arg else Path.cwd()

    if not positional:
        cmd_toggle(project_path)
        return

    cmd = positional[0].lower()

    if cmd == "setup":
        provider = positional[1].lower() if len(positional) > 1 else None
        cmd_setup(provider)
    elif cmd == "toggle":
        cmd_toggle(project_path)
    elif cmd == "claude":
        cmd_claude(project_path)
    elif cmd == "status":
        cmd_status(project_path)
    else:
        backends = load_backends()
        if cmd in backends:
            cmd_switch(cmd, backends, project_path)
        else:
            print(f"Unknown command: {cmd}")
            print(f"Available providers: {', '.join(backends)}")
            print(USAGE)
            sys.exit(1)


if __name__ == "__main__":
    main()
