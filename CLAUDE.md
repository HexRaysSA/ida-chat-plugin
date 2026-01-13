# IDA Chat

Chat interface for IDA Pro powered by Claude Agent SDK.

## Project Structure

```
ida_chat_core.py      # Shared: Agent SDK, script execution, agentic loop
ida_chat_cli.py       # CLI: terminal I/O, arg parsing
ida_chat_plugin.py    # Plugin: Qt UI, IDA integration, markdown rendering
PROMPT.md             # System prompt (loaded at runtime)
USAGE.md              # API usage patterns and tips
API_REFERENCE.md      # Complete ida-domain API reference
```

## Architecture

### Core Module (`ida_chat_core.py`)

Shared foundation for CLI and Plugin:
- `ChatCallback` protocol - abstracts output handling
- `IDAChatCore` class - Agent SDK integration, agentic loop
- Loads system prompt from `PROMPT.md`
- Extracts and executes `<idascript>` blocks
- Feeds script output back to agent until task complete

### CLI Tool (`ida_chat_cli.py`)

Standalone command-line chat for testing outside IDA:

```bash
uv run python ida_chat_cli.py <binary.i64>              # Interactive mode
uv run python ida_chat_cli.py <binary.i64> -p "prompt"  # Single prompt
```

Implements `CLICallback` for terminal output (ANSI colors, `[Thinking...]` indicator).

### IDA Plugin (`ida_chat_plugin.py`)

Dockable chat widget inside IDA Pro (Ctrl+Shift+C to toggle).

Features:
- `PluginCallback` - emits Qt signals for UI updates
- `AgentWorker(QThread)` - runs async agent in background
- Status indicators: blinking orange (processing) → green (complete)
- Markdown rendering via `QTextBrowser` (headers, code, lists, links)
- Thread-safe script execution via `ida_kernwin.execute_sync()`

## How It Works

1. Database opened with `ida_domain.Database.open()`
2. `IDAChatCore` connects to Claude via `ClaudeSDKClient`
3. Agent reads `USAGE.md` and `API_REFERENCE.md` for API knowledge
4. Agent generates analysis code in `<idascript>` XML tags
5. Core extracts scripts and runs `exec(code, {"db": db})`
6. Script output fed back to agent for next iteration
7. Loop continues until agent responds without `<idascript>` tags

## Key Pattern: Agentic Loop

The agent works autonomously until task completion:

```
User: "Analyze this binary"
  → Agent reads docs, writes script
  → Script executes, output returned to agent
  → Agent sees output, writes more scripts or concludes
  → Loop until no more <idascript> tags (max 20 turns)
```

## `<idascript>` Tags

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
zip -r ida-chat.zip ida-plugin.json ida_chat_plugin.py ida_chat_core.py \
    PROMPT.md USAGE.md API_REFERENCE.md
hcli plugin install ida-chat.zip
```

## Logging

Debug logs written to `/tmp/ida-chat.log` for troubleshooting agent behavior.
