"""
IDA Chat Core - Shared foundation for CLI and Plugin.

This module contains the common Agent SDK integration, script execution,
and message processing used by both the CLI and IDA plugin.
"""

import json
import logging
import os
import shutil
import sys
import sysconfig
import tempfile
import uuid
from pathlib import Path
from typing import Protocol, TYPE_CHECKING

import claude_code_transcripts

if TYPE_CHECKING:
    from ida_chat_history import MessageHistory

# Set up debug logging to file
LOG_FILE = Path("/tmp/ida-chat.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a", encoding="utf-8"),
    ]
)
logger = logging.getLogger("ida-chat")

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    HookMatcher,
    AssistantMessage,
    UserMessage,
    TextBlock,
    ThinkingBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
)


# Project directory for agent SDK (contains PROMPT.md, USAGE.md, IDA.md)
PROJECT_DIR = Path(__file__).parent.resolve() / "project"

# Prompt file locations
PROMPT_FILE = PROJECT_DIR / "PROMPT.md"
IDA_UI_FILE = PROJECT_DIR / "IDA.md"
USAGE_FILE = PROJECT_DIR / "USAGE.md"

# ida-nexus mcp exposes the IDA runtime as a stdio MCP server. The Agent SDK
# runs this server, executes the tools, and feeds results back to the model.
MCP_SERVER_NAME = "ida"
EXECUTE_TOOL = f"mcp__{MCP_SERVER_NAME}__execute_python"
MCP_TOOLS = [
    f"mcp__{MCP_SERVER_NAME}__reference",
    f"mcp__{MCP_SERVER_NAME}__open_database",
    EXECUTE_TOOL,
    f"mcp__{MCP_SERVER_NAME}__list_databases",
    f"mcp__{MCP_SERVER_NAME}__save_database",
    f"mcp__{MCP_SERVER_NAME}__close_database",
]


def _find_console_script(name: str) -> str | None:
    """Locate a pip-installed console script for the current interpreter.

    Inside IDA, `sys.executable` is the IDA binary, not a Python interpreter, so
    we cannot launch the server with `sys.executable -m ...`. The console script
    installed by pip carries the correct interpreter in its shebang (or is an
    `.exe` wrapper on Windows), so running it directly always works.
    """
    dirs: list[str] = []
    scripts_dir = sysconfig.get_path("scripts")
    if scripts_dir:
        dirs.append(scripts_dir)
    for prefix in dict.fromkeys([sys.prefix, sys.base_prefix]):
        dirs.append(os.path.join(prefix, "Scripts" if os.name == "nt" else "bin"))

    exe_names = [f"{name}.exe", name] if os.name == "nt" else [name]
    for directory in dirs:
        for exe in exe_names:
            candidate = os.path.join(directory, exe)
            if os.path.isfile(candidate):
                return candidate
    return shutil.which(name)


def _mcp_server_config(db_path: str | None = None) -> dict:
    """Build the stdio launch config for the ida-nexus mcp server.

    The server is launched via its `ida-nexus mcp` console script, located
    for the current interpreter. This works inside IDA, where `sys.executable`
    is the IDA binary rather than a Python interpreter, because the console
    script carries the correct interpreter in its shebang / `.exe` wrapper.

    When ``db_path`` is given it is passed as ``--database`` so the server opens
    and activates that database on startup; the agent then never needs to call
    open_database itself.
    """
    command = _find_console_script("ida-nexus")
    if not command:
        raise RuntimeError(
            "Could not find the 'ida-nexus' console script for this "
            "Python. Ensure the ida-nexus package is installed."
        )

    args = ["mcp", "--transport", "stdio"]
    if db_path:
        args = [*args, "--database", db_path]
    return {"type": "stdio", "command": command, "args": args}


def _load_system_prompt(db_path: str | None = None) -> str:
    """Load the system prompt from PROMPT.md.

    If running inside IDA Pro (IDA_CHAT_INSIDE_IDA env var is set),
    also appends IDA.md which contains the user interaction API.

    Args:
        db_path: Path to the target database. When provided, the agent is told
            to attach to exactly this path via the ida MCP `open_database` tool
            before running any code.

    Note: the full ida-domain API reference is NOT baked into the prompt (the
    combined prompt would exceed Windows' ~32K command-line character limit,
    since it is passed as a --append-system-prompt CLI argument). The agent
    consults the API on demand via the ida MCP `reference` tool instead.
    """
    prompt = ""

    if PROMPT_FILE.exists():
        prompt = PROMPT_FILE.read_text(encoding="utf-8")
    else:
        logger.warning(f"PROMPT.md not found at {PROMPT_FILE}")
        prompt = (
            "You analyze an IDA database through the `ida` MCP tools "
            "(open_database, reference, execute_python)."
        )

    # Append IDA UI interaction API when running inside IDA
    if os.environ.get("IDA_CHAT_INSIDE_IDA") == "1":
        if IDA_UI_FILE.exists():
            logger.info("Running inside IDA - appending IDA.md to system prompt")
            prompt += "\n\n" + IDA_UI_FILE.read_text(encoding="utf-8")
        else:
            logger.warning(f"IDA.md not found at {IDA_UI_FILE}")

    prompt += "\n\n" + USAGE_FILE.read_text(encoding="utf-8")

    # Tell the agent which database is active. The MCP server opens and
    # activates it on startup, so the agent should NOT call open_database.
    if db_path:
        prompt += (
            f"\n\nTHE TARGET DATABASE IS: {db_path}\n"
            "It is already open and set as the active target, so call "
            "`execute_python` directly - do NOT call `open_database` first. "
            "Only if a call reports there is no active database (e.g. the "
            "instance went away) should you call `list_databases` and "
            "`open_database` with the path above to reconnect."
        )

    # Point the agent at the full reference for on-demand lookups. The working
    # directory is now the IDB's directory (not the project), so the agent can't
    # Read these files directly; the ida MCP `reference` tool serves the full
    # ida-domain API reference instead.
    prompt += (
        "\n\nIMPORTANT: The complete ida-domain API reference is searchable "
        "through the ida MCP `reference` tool. Consult it before writing code "
        "instead of guessing the API shape."
    )
    return prompt


# Bare file tools the agent may use, confined to the IDB directory.
FILE_TOOLS = frozenset({"Read", "Write", "Glob", "Grep"})


def _make_tool_gate(allowed_root: Path):
    """Build the authoritative PreToolUse gate for every tool call.

    ``permission_mode="bypassPermissions"`` auto-approves every tool *before*
    ``allowed_tools`` / ``can_use_tool`` are consulted (see the SDK's own
    ``_get_can_use_tool_shadowed_warning``), so ``allowed_tools`` cannot actually
    restrict anything. The SDK's prescribed way to gate every call is a
    PreToolUse hook — this one. It is default-deny:

    * the ``ida`` MCP tools are always allowed;
    * Read/Write/Glob/Grep are allowed only for paths inside ``allowed_root``
      (the IDB's directory), so the agent can work on files next to the target
      binary and nothing else;
    * every other tool (Bash, Task, WebFetch, WebSearch, Edit, ...) is denied.

    The default branch denies, so an unknown or missing tool name fails closed.
    """
    allowed_root = allowed_root.resolve()
    ida_prefix = f"mcp__{MCP_SERVER_NAME}__"

    async def _gate(input_data, tool_use_id, context):
        if input_data.get('hook_event_name') != 'PreToolUse':
            return {}

        name = input_data.get('tool_name') or ''
        tool_input = input_data.get('tool_input')
        if not isinstance(tool_input, dict):
            tool_input = {}

        def deny(reason: str) -> dict:
            logger.warning(f"Blocked tool call {name!r}: {reason}")
            return {
                'hookSpecificOutput': {
                    'hookEventName': 'PreToolUse',
                    'permissionDecision': 'deny',
                    'permissionDecisionReason': reason,
                }
            }

        # The ida MCP tools are the whole point — always allowed.
        if name.startswith(ida_prefix):
            return {}

        # Read/Write/Glob/Grep may only touch paths inside the IDB directory.
        if name in FILE_TOOLS:
            file_path = tool_input.get('file_path') or tool_input.get('path') or ''
            if file_path:
                try:
                    Path(file_path).resolve().relative_to(allowed_root)
                except ValueError:
                    return deny(
                        "File access is restricted to the database directory: "
                        f"{allowed_root}"
                    )
            return {}

        # Everything else (Bash, Task, WebFetch, WebSearch, Edit, ...) is blocked.
        return deny(
            f"The '{name or 'unknown'}' tool is not available in IDA Chat. Use "
            "the ida MCP tools, or Read/Write/Glob/Grep within the database "
            "directory."
        )

    return _gate


async def _inject_session_meta(input_data, tool_use_id, context):
    """Link ida MCP tool calls to this agent's transcript for nexus telemetry.

    This mirrors the ida-nexus Claude plugin's PreToolUse hook, but runs
    in-process: it injects the SDK session transcript path as `_meta` on the
    tool input. The MCP server promotes `_meta` into its semantic trace, so the
    nexus dashboard can correlate its session with this conversation.
    """
    if input_data.get('hook_event_name') != 'PreToolUse':
        return {}

    tool_input = input_data.get('tool_input')
    if not isinstance(tool_input, dict):
        tool_input = {}

    transcript_path = input_data.get('transcript_path')
    if not (isinstance(transcript_path, str) and transcript_path):
        return {}

    meta = dict(tool_input.get('_meta') or {})
    meta['claude_session_path'] = transcript_path
    updated_input = dict(tool_input)
    updated_input['_meta'] = meta

    return {
        'hookSpecificOutput': {
            'hookEventName': 'PreToolUse',
            'updatedInput': updated_input,
        }
    }


def _describe_tool(name: str, inp: dict) -> tuple[str, str]:
    """Produce a (label, detail) pair for displaying a tool call.

    MCP tool names arrive as ``mcp__ida__<tool>``; we show the bare tool name
    plus its most relevant argument.
    """
    short = name.split("__")[-1] if name.startswith("mcp__") else name
    if short == "reference":
        return short, str(inp.get("query", ""))
    if short == "open_database":
        return short, str(inp.get("path", ""))
    if name == "Read":
        return name, str(inp.get("file_path", ""))
    if name in ("Grep", "Glob"):
        return name, str(inp.get("pattern", ""))
    if name == "Task":
        return name, str(inp.get("description", ""))
    # list_databases / save_database / close_database / anything else.
    return short, ""


def _result_text(content) -> str:
    """Flatten a ToolResultBlock's content into plain text."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    parts = []
    for item in content:
        if isinstance(item, dict):
            parts.append(item.get("text", "") or "")
        else:
            parts.append(str(item))
    return "\n".join(p for p in parts if p)


def _format_execution_result(text: str) -> str:
    """Best-effort prettify of an execute_python result.

    The MCP returns a structured ``{result, stdout, stderr}`` object which the
    SDK serializes to JSON text. Surface stdout / return value / stderr when we
    recognize that shape; otherwise return the raw text (e.g. error strings).
    """
    stripped = text.strip()
    if not stripped.startswith("{"):
        return text
    try:
        data = json.loads(stripped)
    except ValueError:
        return text
    if not isinstance(data, dict):
        return text

    parts: list[str] = []
    stdout = data.get("stdout")
    if stdout:
        parts.append(stdout.rstrip("\n"))
    result = data.get("result")
    if result not in (None, ""):
        parts.append(f"=> {result}")
    stderr = data.get("stderr")
    if stderr:
        parts.append(f"[stderr]\n{stderr.rstrip(chr(10))}")
    return "\n".join(parts) if parts else text


def export_transcript(session_file: Path, output_path: Path) -> None:
    """Export a chat session to HTML files.

    Generates index.html and page-XXX.html files in the same directory as output_path.

    Args:
        session_file: Path to the JSONL session file.
        output_path: Path for the main output HTML file (index.html will be renamed to this).

    Raises:
        FileNotFoundError: If session_file doesn't exist.
        Exception: If HTML generation fails.
    """
    if not session_file.exists():
        raise FileNotFoundError(f"Session file not found: {session_file}")

    output_dir = output_path.parent

    # Generate into a temp directory, then copy all HTML files
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        claude_code_transcripts.generate_html(session_file, tmp_path)

        # Copy index.html to the target path
        generated_html = tmp_path / "index.html"
        if generated_html.exists():
            shutil.copy2(generated_html, output_path)
        else:
            raise RuntimeError("HTML generation failed: index.html not created")

        # Copy all page-XXX.html files
        for page_file in tmp_path.glob("page-*.html"):
            shutil.copy2(page_file, output_dir / page_file.name)

    logger.info(f"Exported transcript to {output_path}")


def export_transcript_to_dir(session_file: Path, output_dir: Path) -> Path:
    """Export a chat session to a directory (with all assets).

    Args:
        session_file: Path to the JSONL session file.
        output_dir: Directory to generate HTML into.

    Returns:
        Path to the generated index.html.

    Raises:
        FileNotFoundError: If session_file doesn't exist.
    """
    if not session_file.exists():
        raise FileNotFoundError(f"Session file not found: {session_file}")

    claude_code_transcripts.generate_html(session_file, output_dir)
    logger.info(f"Exported transcript to {output_dir}")
    return output_dir / "index.html"


async def test_claude_connection() -> tuple[bool, str]:
    """Test Claude connectivity with a fun prompt.

    This is a lightweight test that doesn't require a database or full
    agent configuration. Used by the onboarding panel to verify setup.

    Returns:
        Tuple of (success, message):
        - On success: (True, Claude's joke response)
        - On failure: (False, error message)
    """
    logger.info("Testing Claude connection...")

    options = ClaudeAgentOptions(
        cwd=str(PROJECT_DIR),
        permission_mode="bypassPermissions",
        allowed_tools=[],  # No tools needed for simple test
    )

    client = ClaudeSDKClient(options=options)
    try:
        await client.connect()
        await client.query("Tell me a short (one sentence) joke about reverse engineering")

        response_text = ""
        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_text += block.text

        await client.disconnect()
        logger.info(f"Connection test successful: {response_text[:100]}...")
        return True, response_text.strip()

    except Exception as e:
        logger.error(f"Connection test failed: {e}")
        return False, str(e)


def _format_api_error(message) -> str:
    """Turn an is_error ResultMessage into a clear, user-facing outage message."""
    status = getattr(message, "api_error_status", None)
    subtype = (getattr(message, "subtype", "") or "").strip()
    errors = getattr(message, "errors", None) or []
    detail = (getattr(message, "result", "") or "").strip()
    if not detail:
        detail = "; ".join(e for e in errors if e)

    hint = ""
    if status == 429:
        hint = "The API is rate limited. Wait a moment and try again."
    elif status in (500, 502, 503, 504, 529):
        hint = (
            "The Claude API is temporarily unavailable (possible outage). "
            "Try again shortly."
        )

    head = "Request failed"
    if status:
        head += f" (HTTP {status})"
    elif subtype and subtype != "success":
        head += f" ({subtype})"

    parts = [head]
    if hint:
        parts.append(f"— {hint}")
    msg = " ".join(parts)
    if detail and detail.lower() not in msg.lower():
        msg += f"\n{detail}"
    return msg


def _friendly_stream_error(error: Exception) -> str:
    """Map an SDK/stream exception to a friendly, actionable message."""
    text = str(error) or type(error).__name__
    lowered = text.lower()
    if any(
        k in lowered
        for k in ("overloaded", "529", "503", "502", "504", "unavailable",
                  "timeout", "timed out", "connection")
    ):
        return (
            "The Claude API appears to be unavailable right now "
            f"({text}). Please try again in a moment."
        )
    if "429" in lowered or "rate limit" in lowered:
        return f"Rate limited by the API ({text}). Wait a moment and try again."
    return f"The request failed: {text}"


class ChatCallback(Protocol):
    """Protocol for handling chat output events.

    Implementations of this protocol handle the presentation layer,
    whether that's terminal output (CLI) or Qt widgets (Plugin).
    """

    def on_turn_start(self, turn: int, max_turns: int) -> None:
        """Called at the start of each agentic turn."""
        ...

    def on_thinking(self) -> None:
        """Called when the agent starts processing."""
        ...

    def on_thinking_done(self) -> None:
        """Called when the agent produces first output."""
        ...

    def on_thinking_text(self, text: str) -> None:
        """Called with the agent's extended-thinking/reasoning text, when present."""
        ...

    def on_tool_use(self, tool_name: str, details: str) -> None:
        """Called when the agent uses a non-execute tool (Read, reference, ...)."""
        ...

    def on_text(self, text: str, is_final: bool) -> None:
        """Called when the agent outputs assistant text.

        ``is_final`` is True for the concluding answer — text from an assistant
        message that makes no tool calls, i.e. the turn's final response — and
        False for intermediate narration the agent emits before a tool call.
        """
        ...

    def on_script_code(self, code: str) -> None:
        """Called with the Python passed to execute_python, before it runs."""
        ...

    def on_script_output(self, output: str) -> None:
        """Called with the result of an execute_python call."""
        ...

    def on_error(self, error: str) -> None:
        """Called when an error occurs."""
        ...

    def on_result(self, num_turns: int, cost: float | None) -> None:
        """Called when the agent finishes with stats."""
        ...


class IDAChatCore:
    """Shared chat backend for CLI and Plugin.

    Handles Agent SDK integration, message processing, and script execution.
    Implements an agentic loop that feeds script results back to the agent.
    Output is delegated to the callback for presentation.
    """

    def __init__(
        self,
        db_path: str | None,
        callback: ChatCallback,
        verbose: bool = False,
        max_turns: int = 20,
        history: "MessageHistory | None" = None,
        model: str | None = None,
        effort: str | None = None,
    ):
        """Initialize the chat core.

        Args:
            db_path: Path to the target database (executable or .i64). The agent
                attaches to it through the ida MCP `open_database` tool; code is
                executed by the MCP server, not in this process.
            callback: Handler for output events.
            verbose: If True, report additional stats.
            max_turns: Maximum agentic turns before stopping (default 20).
            history: Optional MessageHistory for persisting conversations.
            model: Model alias/id to use (e.g. "sonnet", "opus", "haiku"), or
                None for the Claude Code default.
            effort: Reasoning effort level ("low", "medium", "high", "xhigh",
                "max"), or None for the model default. Guides thinking depth.
        """
        self.db_path = str(db_path) if db_path else None
        self.callback = callback
        self.verbose = verbose
        self.max_turns = max_turns
        self.history = history
        self.model = model
        self.effort = effort
        self.client: ClaudeSDKClient | None = None
        self._cancelled = False
        # Correlates this chat with the ida-nexus telemetry session.
        self._nexus_id = uuid.uuid4().hex[:12]

    async def set_model(self, model: str | None) -> None:
        """Switch the model for subsequent queries (no reconnect needed)."""
        self.model = model
        if self.client:
            await self.client.set_model(model)
            logger.info(f"Model switched to {model!r}")

    async def interrupt(self) -> None:
        """Interrupt the active request out-of-band.

        Callable from another thread via ``run_coroutine_threadsafe`` so a Stop
        works even while ``receive_response()`` is blocked waiting out API
        retries (the in-loop cancel check never runs during that wait).
        """
        self._cancelled = True
        if self.client:
            try:
                await self.client.interrupt()
            except Exception as error:  # noqa: BLE001 - interrupt is best-effort
                logger.warning(f"interrupt failed: {error}")

    def request_cancel(self) -> None:
        """Request cancellation of the current operation."""
        self._cancelled = True
        logger.info("Cancel requested")

    async def connect(self) -> None:
        """Initialize and connect the Agent SDK client."""
        logger.info("=" * 60)
        logger.info("Connecting to Claude Agent SDK")
        logger.info(f"CWD: {PROJECT_DIR}")

        # Tag the nexus session so its telemetry records this chat. The MCP
        # subprocess inherits this env (it is spawned by the SDK's CLI, which
        # inherits ours).
        os.environ["IDA_NEXUS_ID"] = self._nexus_id

        # The agent works out of — and is sandboxed to — the IDB's directory,
        # so it can read/write files sitting next to the target binary and
        # nothing else. The project docs are never the cwd or sandbox: their
        # content is baked into the system prompt and the ida-domain API is
        # served by the ida MCP `reference` tool.
        if not self.db_path:
            raise RuntimeError("A database path is required to connect the agent.")
        working_dir = Path(self.db_path).resolve().parent
        logger.info(f"Agent working directory (sandbox): {working_dir}")

        options = ClaudeAgentOptions(
            cwd=str(working_dir),
            # Load NO filesystem settings: no user/project/local skills, MCP
            # servers, hooks, or permissions. Only what we pass explicitly below
            # is active, so the user's own Claude config never leaks in.
            setting_sources=[],
            # `tools` sets the BASE toolset the model is even offered (the CLI's
            # --tools flag). Left as None it defaults to the full claude_code set
            # (Bash, Task, WebFetch, ...), which is why the agent could shell out.
            # Restricting it to these four built-ins means Bash/Task/etc. are
            # never presented to the model at all. The ida MCP tools come from
            # `mcp_servers` below (not --tools), so they remain available.
            tools=["Read", "Write", "Glob", "Grep"],
            # `allowed_tools` is a SEPARATE axis: it only auto-approves calls, and
            # under bypassPermissions it is advisory anyway. The authoritative
            # restriction + IDB sandbox is the default-deny `_make_tool_gate`
            # PreToolUse hook below, which also blocks anything --tools might still
            # expose. Defense in depth: base toolset + gate.
            allowed_tools=["Read", "Write", "Glob", "Grep", *MCP_TOOLS],
            permission_mode="bypassPermissions",
            max_turns=self.max_turns,
            model=self.model,
            # Reasoning effort; guides thinking depth. None → model default.
            effort=self.effort,
            mcp_servers={MCP_SERVER_NAME: _mcp_server_config(self.db_path)},
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": _load_system_prompt(self.db_path),
            },
            hooks={
                'PreToolUse': [
                    # Authoritative default-deny gate for EVERY tool call. This is
                    # what actually stops Bash/Task/WebFetch/etc. under
                    # bypassPermissions, and sandboxes file tools to the IDB dir.
                    HookMatcher(matcher='.*', hooks=[_make_tool_gate(working_dir)]),
                    HookMatcher(matcher='mcp__ida__.*', hooks=[_inject_session_meta]),
                ]
            },
        )

        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()
        logger.info("Connected successfully")

    async def disconnect(self) -> None:
        """Disconnect the Agent SDK client."""
        if self.client:
            await self.client.disconnect()
            self.client = None

    def _handle_tool_use(self, block: ToolUseBlock) -> None:
        """Report an agent tool call to the callback and history."""
        name = block.name
        inp = block.input if isinstance(block.input, dict) else {}
        logger.info(f"TOOL USE: {name}")
        logger.debug(f"  Tool input: {block.input}")

        if self.history:
            self.history.append_tool_use(
                name,
                block.input if isinstance(block.input, dict)
                else {"input": str(block.input)},
            )

        # execute_python is the heart of the loop: show the code being run,
        if name == EXECUTE_TOOL:
            self.callback.on_script_code(str(inp.get("code", "")))
            return

        label, details = _describe_tool(name, inp)
        self.callback.on_tool_use(label, details)

    def _handle_tool_result(
        self, block: ToolResultBlock, tool_names: dict[str, str]
    ) -> str | None:
        """Surface a tool result. Returns execute_python output, else None."""
        if self.history:
            self.history.append_tool_result(
                block.tool_use_id,
                block.content if block.content is not None else "",
                bool(block.is_error),
            )

        # Only execute_python results are shown as script output; Read/Grep/etc.
        # results stay behind the scenes, as they did before.
        if tool_names.get(block.tool_use_id) != EXECUTE_TOOL:
            return None

        output = _format_execution_result(_result_text(block.content))
        logger.debug(f"execute_python result:\n{output}")
        if output:
            self.callback.on_script_output(output)
        return output

    async def process_message(self, user_input: str) -> str:
        """Send a message and stream the agent's response to completion.

        The Agent SDK drives the tool-call loop itself: it runs the ida
        MCP tools (open_database, execute_python, ...), feeds each result
        back to the model, and keeps going until the model stops calling
        tools or `max_turns` is reached. We just observe the stream and
        forward events to the callback.

        Args:
            user_input: The user's message/query.

        Returns:
            Combined execute_python outputs as a string.
        """
        if not self.client:
            raise RuntimeError("Client not connected. Call connect() first.")

        logger.info("-" * 60)
        logger.info(f"USER MESSAGE: {user_input[:200]}...")

        if self.history:
            self.history.append_user_message(user_input)

        self._cancelled = False
        self.callback.on_turn_start(1, self.max_turns)
        self.callback.on_thinking()

        logger.debug(f"Sending to agent: {user_input[:200]}...")
        await self.client.query(user_input)

        outputs: list[str] = []
        tool_names: dict[str, str] = {}  # tool_use_id -> tool name
        first_output = True
        produced_text = False  # did the CLI already surface assistant text?
        rate_limit_notified = False

        def leave_thinking() -> None:
            nonlocal first_output
            if first_output:
                self.callback.on_thinking_done()
                first_output = False

        try:
            async for message in self.client.receive_response():
                if self._cancelled:
                    logger.info("Operation cancelled by user")
                    await self.client.interrupt()
                    break

                logger.debug(f"Received message type: {type(message).__name__}")

                if isinstance(message, AssistantMessage):
                    # Text in a message that also calls a tool is intermediate
                    # narration; text in a tool-free message is the final answer.
                    has_tool = any(
                        isinstance(b, ToolUseBlock) for b in message.content
                    )
                    for block in message.content:
                        leave_thinking()

                        if isinstance(block, ToolUseBlock):
                            tool_names[block.id] = block.name
                            self._handle_tool_use(block)
                        elif isinstance(block, ThinkingBlock):
                            thinking = block.thinking.strip()
                            if thinking:
                                self.callback.on_thinking_text(thinking)
                        elif isinstance(block, TextBlock):
                            text = block.text.strip()
                            if text:
                                produced_text = True
                                self.callback.on_text(text, is_final=not has_tool)
                                if self.history:
                                    self.history.append_assistant_message(text)

                elif isinstance(message, UserMessage):
                    # The SDK injects tool results as user messages after it runs
                    # each tool against the MCP server.
                    if isinstance(message.content, list):
                        for block in message.content:
                            if isinstance(block, ToolResultBlock):
                                out = self._handle_tool_result(block, tool_names)
                                if out:
                                    outputs.append(out)

                elif isinstance(message, ResultMessage):
                    logger.info(
                        f"ResultMessage: turns={message.num_turns}, "
                        f"is_error={message.is_error}, subtype={message.subtype}, "
                        f"cost={message.total_cost_usd}"
                    )
                    # Surface an error result only if the CLI did not already
                    # show it as assistant text (avoids a duplicate message).
                    if getattr(message, "is_error", False) and not produced_text:
                        leave_thinking()
                        self.callback.on_error(_format_api_error(message))
                    if message.num_turns >= self.max_turns:
                        logger.warning(f"Reached maximum turns ({self.max_turns})")
                    if self.verbose:
                        self.callback.on_result(
                            message.num_turns, message.total_cost_usd
                        )

                elif type(message).__name__ == "RateLimitEvent":
                    # The SDK is waiting out a rate limit; tell the user rather
                    # than leaving them staring at a silent "Thinking…".
                    if not rate_limit_notified:
                        rate_limit_notified = True
                        self.callback.on_error(
                            "The Claude API is rate limited right now — waiting to "
                            "retry. This may take a moment."
                        )
        except Exception as error:  # noqa: BLE001 - surface any stream failure
            logger.error(f"Error while streaming response: {error}", exc_info=True)
            if not self._cancelled:
                self.callback.on_error(_friendly_stream_error(error))
        finally:
            # Always leave the "thinking" state so the UI never gets stuck on it,
            # even when a turn ends with an error or produces no output.
            leave_thinking()
            if self._cancelled:
                self.callback.on_error("Operation cancelled")

        return "\n".join(outputs) if outputs else ""
