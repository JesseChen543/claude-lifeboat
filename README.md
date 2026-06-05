# claude-code-fallback

Instantly switch Claude Code between your Claude Pro subscription and a fallback API endpoint (DeepSeek or any Anthropic-compatible backend) — with automatic VSCode restart.

Built for Claude Code Pro users who want to keep working when they hit their 5-hour usage limit.

---

## How it works

When you hit your Claude Pro limit, run `switch-model`. It:

1. Writes your fallback API credentials to Windows user environment variables
2. Kills VSCode and relaunches it so the new credentials take effect
3. Claude Code picks up the new endpoint on startup — no manual config needed

Switching back to Claude Pro is the same command.

---

## Requirements

- Windows (uses the registry, PowerShell, and `taskkill`)
- Python 3.10+
- Claude Code with a Pro subscription
- A fallback API key (DeepSeek or any Anthropic-compatible endpoint)

---

## Install

**1. Clone the repo**

```
git clone https://github.com/JesseChen543/claude-code-fallback
```

**2. Copy the files to a folder in your PATH**

Copy these three files into any folder already in your PATH — for example `C:\Users\You\.local\bin\`:

- `switch-model.bat`
- `switch_model.py`
- `backends.json`

That's it. The bat finds the python file automatically using `%~dp0`.

**3. Get a fallback API key**

Sign up at your provider's site and create an API key. For DeepSeek (the default): [platform.deepseek.com](https://platform.deepseek.com) → API Keys → Create.

**4. Store your fallback API key (one-time)**

```
switch-model setup
```

You'll be prompted to paste your API key:

```
DeepSeek (backup) API key: sk-xxxxxxxxxxxxxxxx
```

The key is saved to `~/.claude/.api_keys.json` (git-ignored, never committed). It is never written to `backends.json` or any tracked file.

You can run `switch-model setup <provider>` again at any time to update a stored key.

---

## Usage

```
switch-model                    Toggle between Claude Pro and fallback
switch-model status             Show which provider is active
switch-model <provider>         Force switch to a named provider (e.g. deepseek)
switch-model claude             Force switch back to Claude Pro
switch-model setup [provider]   Store or update a provider's API key
```

When switching, you'll be prompted to save your work before VSCode restarts automatically.

Works with any Claude Code installation:

| Context | How to apply the switch |
|---|---|
| VSCode extension | Auto-restart handled by the tool |
| Claude Code CLI | Open a new terminal after switching |
| Claude desktop app | Restart it manually after switching |

---

## Adding providers

Provider definitions live in `backends.json` — no Python required:

```json
{
  "providers": {
    "deepseek": {
      "label": "DeepSeek (backup)",
      "model": "deepseek-v4-pro[1m]",
      "base_url": "https://api.deepseek.com/anthropic"
    },
    "openrouter": {
      "label": "OpenRouter",
      "model": "anthropic/claude-3.5-sonnet",
      "base_url": "https://openrouter.ai/api/v1"
    }
  }
}
```

Then store the key and switch:

```
switch-model setup openrouter
switch-model openrouter
```

Any provider with an Anthropic-compatible endpoint works.

---

## How the VSCode restart works

The script spawns a hidden PowerShell helper that outlives the VSCode terminal. It waits for the terminal to exit, kills `Code.exe`, injects the new environment variables into its own process, then relaunches VSCode — which inherits the correct credentials on startup.

---

## Security

- Your API key is stored in plaintext at `~/.claude/.api_keys.json`. Keep this file private and never commit it (it is git-ignored by default).
- Credentials are set as Windows user environment variables, visible to all processes running as your user.
- `taskkill /f` force-closes VSCode — save your work before confirming the restart.

---

## Why not use a proxy?

Tools like [deepclaude](https://github.com/aattaran/deepclaude) run a local proxy for mid-session switching without restarting. That's more seamless but requires a background process. This tool is simpler — no proxy, no background service, just environment variables and a restart.
