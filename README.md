# Claude Cowork 3P — GitHub Copilot Gateway Setup

Automate the configuration of [Claude Desktop Cowork on third-party platforms](https://support.claude.com/en/articles/14680729-use-claude-cowork-with-third-party-platforms) to use **GitHub Copilot** as the inference gateway.

## Background

Claude Cowork supports [third-party inference providers](https://claude.com/docs/cowork/3p/configuration) (Amazon Bedrock, Google Vertex AI, Azure Foundry, or any LLM gateway exposing `/v1/messages`). GitHub Copilot qualifies as a gateway — it implements the Anthropic Messages API and gives access to all Claude models included in your Copilot subscription at no extra per-token cost.

Setting this up manually requires enabling Developer Mode, navigating the setup UI, discovering which models are available, and writing the correct JSON into multiple config files. This script does all of that in one command.

For full details on the 3P deployment model, see:
- [Use Claude Cowork with third-party platforms](https://support.claude.com/en/articles/14680729-use-claude-cowork-with-third-party-platforms)
- [Install and configure Claude Cowork with third-party platforms](https://support.claude.com/en/articles/14680741-install-and-configure-claude-cowork-with-third-party-platforms)
- [Cowork on 3P — Overview](https://claude.com/docs/cowork/3p/overview)
- [Cowork on 3P — Configuration reference](https://claude.com/docs/cowork/3p/configuration)

## What the script does

1. **Auto-detects** your GitHub token (`$GITHUB_TOKEN` → `gh auth token` → interactive prompt)
2. **Enables Developer Mode** in Claude Desktop if not already active
3. **Discovers** all Claude models on GitHub Copilot that support `/v1/messages`
4. **Creates a "GitHub Copilot" profile** in the Cowork configLibrary (or updates it if it exists)
5. **Sets it as active** (`appliedId`) so Cowork uses it on next launch
6. **Configures Claude Code** tab settings (`~/.claude/settings.json`) with model overrides and display names

## Prerequisites

- **macOS** (Windows support in code but untested)
- **Claude Desktop** installed ([download](https://claude.ai/download))
- **GitHub CLI** installed and authenticated: `gh auth login`
- **Python 3.6+**

## Usage

```bash
# Auto-detect token from gh CLI (recommended)
python3 configure_cowork_copilot.py

# Via environment variable
GITHUB_TOKEN=gho_xxxxx python3 configure_cowork_copilot.py

# Explicit token (visible in shell history — prefer env var)
python3 configure_cowork_copilot.py --token gho_xxxxx

# Include all model versions (not just one per family)
python3 configure_cowork_copilot.py --all-models

# Skip Claude Code tab configuration
python3 configure_cowork_copilot.py --skip-code-tab
```

After running, **restart Claude Desktop** (Cmd+Q → relaunch).

## Options

| Flag | Description |
|---|---|
| `--token` | GitHub Copilot OAuth token. Prefer `$GITHUB_TOKEN` or `gh auth token` instead (`--token` is visible in shell history) |
| `--all-models` | Include all model versions instead of one per family (see caveat below) |
| `--skip-code-tab` | Don't update `~/.claude/settings.json` |

## Files modified

| File | Purpose |
|---|---|
| `~/Library/Application Support/Claude-3p/developer_settings.json` | Enables Developer Mode |
| `~/Library/Application Support/Claude-3p/configLibrary/_meta.json` | Profile registry — adds "GitHub Copilot" entry |
| `~/Library/Application Support/Claude-3p/configLibrary/<uuid>.json` | Gateway config (URL, token, model list) — permissions set to `0600` |
| `~/.claude/settings.json` | Claude Code tab model overrides and display names |

## Known limitations

### Model picker display

All Opus models show as "Opus 4", all Sonnet models show as "Sonnet 4" —
making it impossible to distinguish claude-opus-4.7 from claude-opus-4.6.

Tracked at: **[anthropics/claude-code#52526](https://github.com/anthropics/claude-code/issues/52526)**

**Workaround:** The script defaults to one model per family (`--all-models` overrides this,
but models from the same family will be indistinguishable in the picker).

### Opus 4.7 incompatible with Cowork

Cowork sends `thinking.type: "enabled"` but Opus 4.7 requires `thinking.type: "adaptive"` +
`output_config.effort`. Every request fails with a 400 error.

Tracked at: **[anthropics/claude-code#52541](https://github.com/anthropics/claude-code/issues/52541)**

### Claude Code CLI requires a proxy

Claude Code CLI does not have a Gateway connection mode like Cowork 3P. It hardcodes `x-api-key` authentication, while GitHub Copilot expects `Authorization: Bearer`. This means Claude Code CLI cannot connect directly to Copilot — a local proxy is required to rewrite the auth header.

Feature request for parity: **[anthropics/claude-code#52572](https://github.com/anthropics/claude-code/issues/52572)**

**Workaround:** The script automatically excludes `claude-opus-4.7` and selects `claude-opus-4.6` instead.

## How it works

The script queries `GET https://api.githubcopilot.com/models` and filters for models
that list `/v1/messages` in their `supported_endpoints` — this is the Anthropic Messages API
format that Cowork uses. GPT and Gemini models are excluded because they don't support
this endpoint.

By default, only the latest compatible version per family is kept (e.g. Opus 4.6, Sonnet 4.6)
plus any 1M-context variants as separate entries. Opus 4.7 is excluded due to
[incompatibility](https://github.com/anthropics/claude-code/issues/52541).
Use `--all-models` to include all compatible versions.
