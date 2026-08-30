# IDA Chat

Chat interface for IDA Pro powered by Claude Agent SDK. Code execution is
delegated to the [`ida-nexus`](https://github.com/HexRaysSA/ida-nexus) MCP server (Code Mode).

## Project Structure

```
ida_chat_core.py      # Shared: Agent SDK integration, MCP wiring, response stream
ida_chat_cli.py       # CLI: terminal I/O, arg parsing
ida_chat_plugin.py    # Plugin entry (no Qt): headless back-off, lazy-loads the UI
ida_chat_ui.py        # Qt UI: dockable chat widget, markdown rendering (GUI-only)
project/              # Prompt/doc sources loaded into the system prompt at startup
  PROMPT.md           # System prompt (loaded at runtime)
  USAGE.md            # API usage patterns and tips
  IDA.md              # IDA UI interaction API (appended when running inside IDA)
```

The agent's working directory (and file-access sandbox) is the **IDB's
directory**, not `project/`. The full ida-domain API reference is served by the
ida MCP `reference` tool rather than shipped as a file.

## Architecture

### Core Module (`ida_chat_core.py`)

Shared foundation for CLI and Plugin:
- `ChatCallback` protocol - abstracts output handling
- `IDAChatCore` class - Agent SDK integration; registers the `ida` MCP server
  and streams the agent's response
- Loads system prompt from `PROMPT.md` (with the target DB path appended)
- Observes the SDK's native tool-call loop and forwards events to the callback

Execution is **not** done in-process anymore. The agent calls the `ida` MCP
tools (`open_database`, `reference`, `execute_python`, ...) and the SDK runs
that loop itself; the core just watches the message stream.

### CLI Tool (`ida_chat_cli.py`)

Standalone command-line chat for testing outside IDA:

```bash
uv run python ida_chat_cli.py <binary.i64>              # Interactive mode
uv run python ida_chat_cli.py <binary.i64> -p "prompt"  # Single prompt
```

Implements `CLICallback` for terminal output (ANSI colors, `[Thinking...]` indicator).

### IDA Plugin (`ida_chat_plugin.py` + `ida_chat_ui.py`)

Dockable chat widget inside IDA Pro (Ctrl+Shift+C to toggle).

**Headless-safe split.** `ida_chat_plugin.py` is a thin entry with **no Qt or
ida_domain imports** — IDA imports the plugin module before `init()` runs, so a
top-level `import PySide6` would fire (and warn) even under idalib/idat. Instead,
`init()` calls `is_interactive_gui()` (`ida_kernwin.is_idaq()` **and**
`IDA_IS_INTERACTIVE=1`, matching the ida-nexus plugin) and returns
`PLUGIN_SKIP` when headless, so Qt is never imported in a batch run. The whole Qt
UI lives in `ida_chat_ui.py`, imported lazily from `toggle_widget()` only in an
interactive GUI. `term()` also short-circuits when headless.

Features (in `ida_chat_ui.py`):
- `PluginCallback` - emits Qt signals for UI updates
- `AgentWorker(QThread)` - runs async agent in background
- Status indicators: blinking orange (processing) → green (complete)
- Markdown rendering via `QTextBrowser` (headers, code, lists, links)

The plugin passes the open database's *path* to the core and lets the MCP run
code (on IDA's main thread), so it no longer needs its own `execute_sync`
executor. Before starting the agent it calls `GuiNexusRegistration.ensure()`,
which registers the **live GUI database** with Code Mode using the `ida_nexus`
package (a pip dependency) — unless a GUI instance for this IDB is already
registered (the standalone `ida-nexus` plugin, or another plugin using this
pattern), in which case it attaches to that. This guarantees the agent sees the
analyst's live state (unsaved renames/comments/types), not a stale idalib worker.
hcli has no plugin-to-plugin dependency mechanism, which is why we self-register
instead of depending on the `ida-nexus` plugin. The nexus plugin performs
the same back-off check, so the two coexist regardless of load order.

## How It Works

1. Host resolves the target DB path (CLI: the argument; plugin: `_nexus_gui_paths()`)
2. Plugin only: `GuiNexusRegistration.ensure()` registers the live GUI DB with
   Code Mode (or attaches to an existing registration)
3. `IDAChatCore` connects to Claude via `ClaudeSDKClient`, registering the `ida`
   MCP server (launched via its `ida-nexus-mcp` console script with
   `--transport stdio --database <path>`) and allowing its tools
4. The MCP opens and activates `<path>` on startup (the registered GUI instance,
   else a managed idalib worker), so the agent does **not** call `open_database`
5. The agent calls `reference(...)` / `execute_python(...)` as MCP tools
6. The Agent SDK runs each tool against the MCP server and feeds results back to
   the model automatically
7. `IDAChatCore.process_message` streams the response, forwarding tool calls and
   `execute_python` output to the callback
8. The loop is bounded by `ClaudeAgentOptions.max_turns`

## Isolation & telemetry

- **No user config leaks in.** `setting_sources=[]` means the SDK loads no
  user/project/local skills, MCP servers, hooks, or permissions. Only the tools,
  `mcp_servers`, `hooks`, and system prompt passed programmatically are active.
- **Session persistence.** The SDK writes the full transcript to
  `~/.claude/projects/<cwd-hash>/<session_id>.jsonl` (plus `agent-*.jsonl` for
  subagents). `MessageHistory` additionally logs to `~/.ida-chat/sessions/` for
  the in-plugin HTML export.
- **Nexus telemetry link.** A `PreToolUse` hook (`_inject_session_meta`,
  matcher `mcp__ida__.*`) injects `_meta.claude_session_path` (the SDK transcript
  path) into every ida tool call. The MCP promotes it into its semantic trace
  (`~/.ida-nexus/sessions/`), and `IDA_NEXUS_ID` (set per session) tags the
  `nexus_id`, so the nexus dashboard correlates its trace with this chat.
  This reimplements, in-process, what the ida-nexus Claude plugin's hook does.

## Key Pattern: SDK-driven tool loop

The agentic loop now lives inside the Agent SDK, not in this repo. A single
`client.query()` + `receive_response()` streams the whole turn:

```
User: "Analyze this binary"
  → Agent: open_database(path)      (MCP tool, run by the SDK)
  → Agent: reference("functions")   (MCP tool)
  → Agent: execute_python(code)     (MCP tool → runs against open DB)
  → SDK feeds each tool result back to the model
  → Agent concludes with a text answer (max_turns cap)
```

`execute_python` runs Python with the ida-domain `Database` available as the
global `db`; a trailing expression is the return value and `print()` is streamed.

## MCP server launch

`_mcp_server_config()` in `ida_chat_core.py` launches the server via its
`ida-nexus-mcp` **console script**, located with `sysconfig.get_path("scripts")`
(and `sys.prefix`/`sys.base_prefix` bin dirs). This is required inside IDA, where
`sys.executable` is the IDA binary, not a Python interpreter — so `<python> -m …`
would make IDA try to open the module name as an input file. The console script's
shebang (`.exe` wrapper on Windows) carries the correct interpreter.

## Dependencies

- `claude-agent-sdk` - Agent SDK for Claude
- `ida-domain` - IDA Pro domain API (works standalone, spawns IDA headlessly)
- `ida-nexus-mcp` - Code Mode MCP server that executes Python against the DB
  (sibling repo; pulled in via `[tool.uv.sources]`, requires Python ≥ 3.11)

## Development

```bash
# Install dependencies
uv sync

# Test CLI (outside IDA)
uv run python ida_chat_cli.py calc.exe.i64 -p "list 3 functions"
```

### Deploying the Plugin to IDA

The plugin must be packaged as a zip and installed via `hcli`. **Close IDA before redeploying.**

```bash
# Redeploy plugin (uninstall old, install new)
rm -f ida-chat.zip && \
zip -r ida-chat.zip ida-plugin.json ida_chat_plugin.py ida_chat_ui.py ida_chat_core.py ida_chat_history.py splash.png project/ && \
hcli plugin uninstall ida-chat && \
hcli plugin install ida-chat.zip --config show_wizard=true --config auth_type=system --config api_key=
```

**Files included in the plugin zip:**
- `ida-plugin.json` - Plugin manifest
- `ida_chat_plugin.py` - Thin plugin entry (no Qt); declines headless, lazy-loads the UI
- `ida_chat_ui.py` - Qt UI (dockable chat widget); imported only in an interactive GUI
- `ida_chat_core.py` - Shared core module
- `ida_chat_history.py` - Message history persistence
- `splash.png` - Onboarding splash image
- `project/` - Agent prompts and documentation (PROMPT.md, USAGE.md, IDA.md)

## Releasing

```bash
# Build the release zip
zip -r ida-chat.zip ida-plugin.json ida_chat_plugin.py ida_chat_ui.py ida_chat_core.py ida_chat_history.py project/

# Create and push the tag
git tag -a X.Y.Z -m "Release message"
git push origin X.Y.Z

# Create GitHub release with artifact
gh release create X.Y.Z ida-chat.zip --title "vX.Y.Z - Title" --generate-notes
```

## Logging

Debug logs written to `/tmp/ida-chat.log` for troubleshooting agent behavior.
