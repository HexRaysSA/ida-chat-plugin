# IDA Chat

Chat interface for IDA Pro powered by Claude Agent SDK.

## Architecture

```
ida_chat_core.py      # Shared: Agent SDK, script execution, message processing
ida_chat_cli.py       # CLI-specific: terminal I/O, arg parsing
ida_chat_plugin.py    # Plugin-specific: Qt UI, IDA integration
```

### Core Module (`ida_chat_core.py`)

Contains shared foundation:
- `ChatCallback` protocol - abstracts output handling
- `IDAChatCore` class - Agent SDK integration, script execution
- Constants: `SYSTEM_PROMPT_APPEND`, `IDASCRIPT_PATTERN`, skill directory path

### CLI Tool (`ida_chat_cli.py`)

Standalone command-line chat for testing outside IDA:

```bash
uv run python ida_chat_cli.py <binary.i64>              # Interactive mode
uv run python ida_chat_cli.py <binary.i64> -p "prompt"  # Single prompt
```

Implements `CLICallback` for terminal output (ANSI colors, `[Thinking...]` indicator).

### IDA Plugin (`ida_chat_plugin.py`)

Dockable chat widget inside IDA Pro (Ctrl+Shift+C to toggle).

Implements:
- `PluginCallback` - emits Qt signals for UI updates
- `AgentWorker(QThread)` - runs async agent in background thread
- Signal/slot mechanism for thread-safe UI updates

## How It Works

1. Database opened with `ida_domain.Database.open()`
2. `IDAChatCore` connects to Claude via `ClaudeSDKClient`
3. Agent generates analysis code in `<idascript>` XML tags
4. Core extracts scripts and runs `exec(code, {"db": db})`
5. Output routed through `ChatCallback` to presentation layer

## Key Pattern: `<idascript>` Tags

Agent outputs analysis code in XML tags:

```xml
<idascript>
for func in db.functions:
    name = db.functions.get_name(func)
    print(f"{name}: 0x{func.start_ea:08X}")
</idascript>
```

The core module parses these tags and executes the code against the open `db` instance.

## Dependencies

- `claude-agent-sdk` - Agent SDK for Claude
- `ida-domain` - IDA Pro domain API (works standalone, spawns IDA headlessly)

## Development

```bash
# Install dependencies
uv sync

# Test CLI
uv run python ida_chat_cli.py calc.exe.i64 -p "list 3 functions"

# Install plugin to IDA
zip -r ida-chat.zip ida-plugin.json ida_chat_plugin.py ida_chat_core.py
hcli plugin install ida-chat.zip
```
