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
"""

import json
import re
import shlex
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

def _find_vscode_cli() -> str | None:
    """Prefer the .cmd wrapper over the exe — it handles single-instance IPC correctly."""
    candidates = [
        Path.home() / "AppData/Local/Programs/Microsoft VS Code/bin/code.cmd",
        Path("C:/Program Files/Microsoft VS Code/bin/code.cmd"),
        Path.home() / "AppData/Local/Programs/Microsoft VS Code Insiders/bin/code-insiders.cmd",
        Path.home() / "AppData/Local/Programs/cursor/resources/app/bin/cursor.cmd",
        Path.home() / "AppData/Local/cursor/resources/app/bin/cursor.cmd",
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    for name in ("code", "cursor"):
        cmd = shutil.which(name)
        if cmd:
            return cmd
    return None


def _resolve_workspace(path: str) -> list[str]:
    """Expand untitled internal workspaces into individual folders; leave named .code-workspace files as-is."""
    roaming = Path.home() / "AppData/Roaming"
    internal_dirs = [roaming / "Code/Workspaces", roaming / "Code - Insiders/Workspaces"]
    p = Path(path)
    if not any(p.is_relative_to(d) for d in internal_dirs if d.exists()):
        return [path]
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        existing = [f["path"] for f in data.get("folders", []) if "path" in f and Path(f["path"]).exists()]
        if existing:
            return existing
    except Exception:
        pass
    return [path]


def _get_open_workspaces_from_status(code_cli: str) -> list[list[str]]:
    """Parse 'code --status' Workspace Stats for open folders; resolve base-names via storage.json."""
    try:
        result = subprocess.run([code_cli, "--status"], capture_output=True, text=True, timeout=15)

        folder_names: list[str] = []
        in_stats = False
        for line in result.stdout.splitlines():
            if "Workspace Stats:" in line:
                in_stats = True
                continue
            if in_stats:
                m = re.search(r'\|\s+Folder \(([^)]+)\):', line)
                if m:
                    folder_names.append(m.group(1))
        if not folder_names:
            return []

        storage_path = Path.home() / "AppData/Roaming/Code/User/globalStorage/storage.json"
        if not storage_path.exists():
            return []

        # Build a (basename, full_path) pool from windowsState so two folders
        # with the same basename each get their own full path (consume-on-match).
        data = json.loads(storage_path.read_text(encoding="utf-8"))
        pool: list[tuple[str, str]] = []
        all_windows = [data.get("windowsState", {}).get("lastActiveWindow")] + \
                      data.get("windowsState", {}).get("openedWindows", [])
        for win in all_windows:
            if not win:
                continue
            uri = (win.get("folder") or win.get("workspace", {}).get("configPath")
                   or win.get("workspaceIdentifier", {}).get("configURIPath") or "")
            if not uri:
                continue
            try:
                path = unquote(urlparse(uri).path).lstrip("/")
                if Path(path).exists():
                    pool.append((Path(path).name, path.replace("\\", "/")))
            except Exception:
                pass

        seen: set[str] = set()
        groups: list[list[str]] = []
        for name in folder_names:
            # Find and consume the first pool entry matching this basename
            idx = next((i for i, (n, _) in enumerate(pool) if n == name), None)
            if idx is None:
                continue
            _, full = pool.pop(idx)
            if full not in seen:
                seen.add(full)
                groups.append([full])
        # If we couldn't resolve every open folder, fall through to the next
        if len(groups) < len(folder_names):
            return []
        return groups
    except Exception:
        return []


def _parse_vscode_cmdlines(cmdlines: list[str]) -> list[list[str]]:
    """Extract open folder paths from VSCode main-process command lines."""
    seen: set[str] = set()
    groups: list[list[str]] = []
    for line in cmdlines:
        line = line.strip()
        if not line:
            continue
        try:
            args = shlex.split(line, posix=False)
        except ValueError:
            args = line.split()
        args = [a.strip('"').strip("'") for a in args]
        for arg in args[1:]:  # skip executable
            if arg.startswith("-"):
                continue
            p = Path(arg)
            if (p.exists() and p.is_dir()) or (p.is_file() and p.suffix == ".code-workspace"):
                norm = str(p).replace("\\", "/")
                if norm not in seen:
                    seen.add(norm)
                    groups.append([norm])
    return groups


def _get_open_workspaces_from_storage() -> list[list[str]]:
    """Fallback: read open workspaces from VSCode's storage.json."""
    candidates = [
        Path.home() / "AppData/Roaming/Code/User/globalStorage/storage.json",
        Path.home() / "AppData/Roaming/Code - Insiders/User/globalStorage/storage.json",
    ]
    storage = next((p for p in candidates if p.exists()), None)
    if storage is None:
        return []
    try:
        data = json.loads(storage.read_text(encoding="utf-8"))
        seen: set[str] = set()
        groups: list[list[str]] = []
        for win in [data.get("windowsState", {}).get("lastActiveWindow")] + data.get("windowsState", {}).get("openedWindows", []):
            if not win:
                continue
            uri = (win.get("folder") or win.get("workspace", {}).get("configPath")
                   or win.get("workspaceIdentifier", {}).get("configURIPath") or "")
            if not uri:
                continue
            path = unquote(urlparse(uri).path).lstrip("/")
            if not Path(path).exists():
                continue
            for resolved in _resolve_workspace(path):
                if resolved not in seen:
                    seen.add(resolved)
                    groups.append([resolved])
        return groups
    except Exception:
        return []


def _get_open_workspaces() -> list[list[str]]:
    """Return one [folder] group per open VSCode window, using the best available source."""
    # code --status sees all windows including those opened via the UI
    cli = _find_vscode_cli()
    if cli:
        groups = _get_open_workspaces_from_status(cli)
        if groups:
            return groups
    # WMI captures command-line paths but misses windows opened via the UI
    try:
        result = subprocess.run(
            ["powershell", "-Command",
             "Get-WmiObject Win32_Process -Filter \"name='Code.exe'\" | "
             "Where-Object { $_.CommandLine -and "
             "$_.CommandLine -notmatch '--type=' -and "
             "$_.CommandLine -notmatch '\\.js\\b' -and "
             "$_.CommandLine -notmatch 'server\\.bundle' } | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            groups = _parse_vscode_cmdlines(result.stdout.strip().splitlines())
            if groups:
                return groups
    except Exception:
        pass
    return _get_open_workspaces_from_storage()


def restart_vscode(prompt: bool = True) -> None:
    if prompt:
        print("\nSave any unsaved work in VSCode first.")
        answer = input("Restart VSCode now? [Y/n]: ").strip().lower()
        if answer and answer not in ("y", "yes"):
            print("  Skipped — restart VSCode manually to apply the change.")
            return

    code_cli = _find_vscode_cli()
    if not code_cli:
        print("  Could not find VSCode/Cursor CLI — open it manually.")
        return

    window_groups = _get_open_workspaces()

    def ps_escape(s: str) -> str:
        return s.replace("'", "''")

    if window_groups:
        launch_lines = []
        for i, group in enumerate(window_groups):
            for path in group:
                launch_lines.append(f"& '{ps_escape(code_cli)}' '{ps_escape(path)}'")
            if i < len(window_groups) - 1:
                launch_lines.append("Start-Sleep -Milliseconds 500")
        launch = "\n".join(launch_lines)
    else:
        launch = f"& '{ps_escape(code_cli)}'"

    helper = Path.home() / ".claude" / "_restart_vscode.ps1"
    helper.parent.mkdir(parents=True, exist_ok=True)
    # Inject user env vars before launching VSCode — Start-Process inherits the
    # helper's env, not the registry, so we sync them explicitly.
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
        f"Remove-Item '{ps_escape(str(helper))}'\n",
        encoding="utf-8",
    )

    # CREATE_NEW_CONSOLE lets the helper survive when VSCode (and this terminal) is killed.
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
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


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
    switch-model <provider>         Force switch to named provider (see backends.json)"""


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
