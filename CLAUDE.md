# IDA Chat

Chat interface for IDA Pro powered by Claude Agent SDK.

## Components

### 1. CLI Tool (`ida_chat_cli.py`)

Standalone command-line chat for testing outside IDA:

```bash
uv run python idachat.py <binary.i64>           # Interactive mode
uv run python idachat.py <binary.i64> -p "..."  # Single prompt
```

**How it works:**
1. Opens database with `ida_domain.Database.open()` at startup
2. Connects to Claude via `ClaudeSDKClient` with `cwd` pointing to ida-domain skill directory
3. Agent generates analysis code wrapped in `<idascript>` tags
4. CLI extracts scripts and runs `exec(code, {"db": db})` against the open database
5. Output is captured and displayed

### 2. IDA Plugin (`ida_chat_plugin.py`)

Dockable chat widget inside IDA Pro (Ctrl+Shift+C to toggle).

Currently has basic "list functions" command. Will be extended to use the same ClaudeSDKClient backend as the CLI.

## Key Pattern: `<idascript>` Tags

The agent outputs analysis code in XML tags:

```xml
<idascript>
for func in db.functions:
    name = db.functions.get_name(func)
    print(f"{name}: 0x{func.start_ea:08X}")
</idascript>
```

The host (CLI or plugin) parses these tags and executes the code against the open `db` instance.

## Dependencies

- `claude-agent-sdk` - Agent SDK for Claude
- `ida-domain` - IDA Pro domain API (works standalone, spawns IDA headlessly)

## Development

```bash
# Install dependencies
uv sync

# Test CLI
uv run python ida_chat_cli.py calc.exe.i64 -p "list functions"

# Install plugin to IDA
cd ~/.claude/plugins/cache/ida-claude-plugins/ida-plugin/1.0.0/skills/ida-plugin
uv run python package.py /path/to/this/folder --install
```
