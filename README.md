# claude-lifeboat

Instantly switch Claude Code between your Claude Pro subscription and a fallback API endpoint (DeepSeek or any Anthropic-compatible backend) — per project, no restart needed.

Built for Claude Code Pro users who want to keep working when they hit their 5-hour usage limit, or run two projects simultaneously on different models.

---

## How it works

When you hit your Claude Pro limit (or want a different model for a project), run `switch-model` from that project's directory. It:

1. Writes your fallback API credentials to `.vscode/settings.json` in the current project
2. VSCode picks up the change immediately — no restart needed

Each project gets its own setting. Two projects can run different models at the same time.

---

## Requirements

- Windows
- Python 3.10+
- Claude Code with a Pro subscription
- A fallback API key (DeepSeek or any Anthropic-compatible endpoint)

---

## Install

**1. Clone the repo**

```
git clone https://github.com/JesseChen543/claude-lifeboat
cd claude-lifeboat
```

**2. Run the installer**

```
.\install.ps1
```

This copies `switch-model.bat`, `switch_model.py`, and `backends.json` to `%LOCALAPPDATA%\Microsoft\WindowsApps\`, which is already on your PATH — no PATH changes needed.

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

The key is saved to `~/.claude/.api_keys.json` (git-ignored, never committed).

---

## Usage

All commands operate on the current directory by default. Use `--project <path>` to target a different folder.

```
switch-model                       Toggle between Claude Pro and fallback
switch-model <provider>            Switch to a named provider (e.g. deepseek)
switch-model claude                Switch back to Claude Pro
switch-model status                Show active provider for this project
switch-model setup [provider]      Store or update a provider's API key
switch-model --project <path>      Target a specific project directory
```

Each project's `.vscode/settings.json` is updated independently — open multiple projects in VSCode and they'll use different backends simultaneously.

---

## Adding providers

Provider definitions live in `backends.json`:

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

## Security

- Your API key is stored in plaintext at `~/.claude/.api_keys.json`. Keep this file private and never commit it (it is git-ignored by default).
- Credentials are written to `.vscode/settings.json` in each project. Don't commit that file if the project is public — add `.vscode/settings.json` to your `.gitignore`.
