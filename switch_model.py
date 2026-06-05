#!/usr/bin/env python3
"""
Switch Claude Code between Claude Pro (built-in auth) and any Anthropic-compatible backend.

Provider definitions live in backends.json (next to this script).
API keys are stored in ~/.claude/.api_keys.json (git-ignored).

Usage:
    switch-model                    Toggle between providers (prompts to restart)
    switch-model setup [provider]   Store a provider's API key (one-time)
    switch-model status             Show current active provider
    switch-model claude             Force switch to Claude Pro
    switch-model <provider>         Force switch to a named provider
    switch-model restart-vscode     Restart VSCode without switching provider
"""

import json
import shutil
import subprocess
import sys
import winreg
from pathlib import Path
from urllib.parse import unquote, urlparse

KEYS_FILE = Path.home() / ".claude" / ".api_keys.json"
BACKENDS_FILE = Path(__file__).parent / "backends.json"

BACKEND_VARS = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "ANTHROPIC_MODEL")

VSCODE_PROCESSES = ["Code.exe", "Code - Insiders.exe"]


# ── Windows user env helpers ──────────────────────────────────────────

def get_user_env(name: str) -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, name)
            return value
    except FileNotFoundError:
        return None


def set_user_env(name: str, value: str | None) -> None:
    if value is None:
        ps_cmd = f'[System.Environment]::SetEnvironmentVariable("{name}", $null, "User")'
    else:
        # Single-quoted PS string: only escape needed is '' for literal '
        # Avoids $-expansion that would corrupt keys containing dollar signs
        escaped = value.replace("'", "''")
        ps_cmd = f"[System.Environment]::SetEnvironmentVariable(\"{name}\", '{escaped}', \"User\")"
    subprocess.run(["powershell", "-Command", ps_cmd], check=True, capture_output=True)


# ── VSCode restart ────────────────────────────────────────────────────

def _find_vscode() -> str | None:
    candidates = [
        Path.home() / "AppData/Local/Programs/Microsoft VS Code/Code.exe",
        Path("C:/Program Files/Microsoft VS Code/Code.exe"),
        Path.home() / "AppData/Local/Programs/Microsoft VS Code Insiders/Code - Insiders.exe",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    code_cmd = shutil.which("code")
    if code_cmd:
        return code_cmd
    return None


def _get_open_workspaces() -> list[str]:
    """Read open workspace folders from VSCode's storage.json before killing."""
    candidates = [
        Path.home() / "AppData/Roaming/Code/User/globalStorage/storage.json",
        Path.home() / "AppData/Roaming/Code - Insiders/User/globalStorage/storage.json",
    ]
    storage = next((p for p in candidates if p.exists()), None)
    if storage is None:
        return []
    try:
        data = json.loads(storage.read_text(encoding="utf-8"))
        state = data.get("windowsState", {})
        seen: set[str] = set()
        folders: list[str] = []
        for win in [state.get("lastActiveWindow")] + state.get("openedWindows", []):
            if not win:
                continue
            uri = win.get("folder") or win.get("workspace", {}).get("configPath", "")
            if not uri:
                continue
            path = unquote(urlparse(uri).path).lstrip("/")
            if Path(path).exists() and path not in seen:
                seen.add(path)
                folders.append(path)
        return folders
    except Exception:
        return []


def restart_vscode(prompt: bool = True) -> None:
    if prompt:
        print("\nSave any unsaved work in VSCode first.")
        answer = input("Restart VSCode now? [Y/n]: ").strip().lower()
        if answer and answer not in ("y", "yes"):
            print("  Skipped — restart VSCode manually to apply the change.")
            return

    code_exe = _find_vscode()
    workspaces = _get_open_workspaces()

    if not code_exe:
        print("  Could not find VSCode executable — open it manually.")
        return

    # Build the relaunch command — escape single quotes for PowerShell string literals
    def ps_escape(s: str) -> str:
        return s.replace("'", "''")

    if workspaces:
        workspace_args = "','".join(ps_escape(w) for w in workspaces)
        launch = f"Start-Process -FilePath '{ps_escape(code_exe)}' -ArgumentList @('{workspace_args}')"
    else:
        launch = f"Start-Process -FilePath '{ps_escape(code_exe)}'"

    # Write a detached helper script that survives VSCode's terminal closing.
    # It waits 1s for the terminal to exit, kills VSCode, waits for processes
    # to fully terminate, then relaunches and deletes itself.
    helper = Path.home() / ".claude" / "_restart_vscode.ps1"
    helper.parent.mkdir(parents=True, exist_ok=True)
    # Inject user env vars into the helper's process before launching VSCode,
    # because Start-Process inherits the helper's env — not the registry.
    sync_env = "\n".join(
        f'$env:{v} = [System.Environment]::GetEnvironmentVariable("{v}", "User")'
        for v in BACKEND_VARS
    )
    kill_cmds = "\n".join(f'taskkill /f /im "{proc}" 2>$null' for proc in VSCODE_PROCESSES)
    helper.write_text(
        f"Start-Sleep 1\n"
        f"{kill_cmds}\n"
        f"Start-Sleep 2\n"
        f"{sync_env}\n"
        f"{launch}\n"
        f"Remove-Item '{helper}'\n",
        encoding="utf-8"
    )

    # Launch in a new independent console so it survives when VSCode (and its
    # terminal) is killed. CREATE_NEW_CONSOLE gives it its own window/session.
    subprocess.Popen(
        ["powershell", "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass", "-File", str(helper)],
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )

    print("  VSCode restarting...")


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
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    path.write_text(payload, encoding="utf-8")


def load_backends() -> dict:
    data = load_json(BACKENDS_FILE)
    providers = data.get("providers", {})
    if not providers:
        print(f"No providers defined in {BACKENDS_FILE}")
        sys.exit(1)
    return providers


def _active_provider(backends: dict) -> str:
    auth_token = get_user_env("ANTHROPIC_AUTH_TOKEN")
    base_url = (get_user_env("ANTHROPIC_BASE_URL") or "").strip().rstrip("/")
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
            cmd_switch(provider, backends)


def cmd_toggle():
    backends = load_backends()
    active = _active_provider(backends)
    if active == "claude":
        provider = next(iter(backends))
        cmd_switch(provider, backends)
    else:
        cmd_claude()


def cmd_switch(provider: str, backends: dict):
    if _active_provider(backends) == provider:
        print(f"Already using {backends[provider]['label']} — nothing to change.")
        return

    cfg = backends[provider]
    keys = load_json(KEYS_FILE)
    api_key = keys.get(provider)
    if not api_key:
        print(f"No API key stored for {provider}. Run: switch-model setup {provider}")
        sys.exit(1)

    set_user_env("ANTHROPIC_AUTH_TOKEN", api_key)
    set_user_env("ANTHROPIC_MODEL", cfg["model"])
    set_user_env("ANTHROPIC_BASE_URL", cfg["base_url"])

    print(f"OK Now using: {cfg['label']}")
    print(f"  Model:    {cfg['model']}")
    print(f"  Endpoint: {cfg['base_url']}")
    restart_vscode()


def cmd_claude():
    removed = []
    for var in BACKEND_VARS:
        if get_user_env(var) is not None:
            set_user_env(var, None)
            removed.append(var)

    if removed:
        print("OK Now using: Claude Pro (built-in auth)")
        restart_vscode()
    else:
        print("Already using Claude Pro — nothing to change.")


def cmd_status():
    backends = load_backends()
    model = get_user_env("ANTHROPIC_MODEL")
    base_url = get_user_env("ANTHROPIC_BASE_URL") or ""

    active = _active_provider(backends)
    if active == "claude":
        provider_label = "Claude Pro (built-in auth)"
    else:
        provider_label = backends[active]["label"]

    print(f"Provider: {provider_label}")
    if model:
        print(f"Model:    {model}")
    if base_url:
        print(f"Endpoint: {base_url}")
    print(f"Scope:    Windows user environment variables")

    keys = load_json(KEYS_FILE)
    for name, cfg in backends.items():
        stored = "OK set" if keys.get(name) else f"MISSING (run: switch-model setup {name})"
        print(f"{cfg['label']} key: {stored}")


# ── Main ──────────────────────────────────────────────────────────────

USAGE = """Usage:
    switch-model                    Toggle between providers
    switch-model setup [provider]   Store a provider's API key (one-time)
    switch-model status             Show current provider
    switch-model claude             Force switch to Claude Pro
    switch-model <provider>         Force switch to named provider (see backends.json)
    switch-model restart-vscode     Restart VSCode without switching"""


def main():
    args = sys.argv[1:]
    cmd = args[0].lower() if args else "toggle"

    if cmd == "setup":
        provider = args[1].lower() if len(args) > 1 else None
        cmd_setup(provider)
    elif cmd == "toggle":
        cmd_toggle()
    elif cmd == "claude":
        cmd_claude()
    elif cmd == "status":
        cmd_status()
    elif cmd == "restart-vscode":
        restart_vscode(prompt=False)
    else:
        backends = load_backends()
        if cmd in backends:
            cmd_switch(cmd, backends)
        else:
            print(f"Unknown command: {cmd}")
            print(f"Available providers: {', '.join(backends)}")
            print(USAGE)
            sys.exit(1)


if __name__ == "__main__":
    main()
