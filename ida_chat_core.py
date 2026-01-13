"""
IDA Chat Core - Shared foundation for CLI and Plugin.

This module contains the common Agent SDK integration, script execution,
and message processing used by both the CLI and IDA plugin.
"""

import re
import sys
from io import StringIO
from pathlib import Path
from typing import Callable, Protocol

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
)


# Path to ida-domain skill directory for loading skills
IDA_DOMAIN_SKILL_DIR = (
    Path.home()
    / ".claude/plugins/cache/ida-claude-plugins/ida-domain/1.0.0/skills/ida-domain-scripting"
)

# Regex to extract <idascript>...</idascript> blocks
IDASCRIPT_PATTERN = re.compile(r"<idascript>(.*?)</idascript>", re.DOTALL)

# System prompt addition for the agent
SYSTEM_PROMPT_APPEND = """
You have access to an open IDA database via the `db` variable.
When you need to query or analyze the binary, output Python code in <idascript> tags.
The code will be exec()'d with `db` in scope. Use print() for output.

Example:
<idascript>
for i, func in enumerate(db.functions):
    if i >= 10:
        break
    name = db.functions.get_name(func)
    print(f"{name}: 0x{func.start_ea:08X}")
</idascript>

Always wrap analysis code in <idascript> tags. The output from print() will be shown to the user.
"""


class ChatCallback(Protocol):
    """Protocol for handling chat output events.

    Implementations of this protocol handle the presentation layer,
    whether that's terminal output (CLI) or Qt widgets (Plugin).
    """

    def on_thinking(self) -> None:
        """Called when the agent starts processing."""
        ...

    def on_thinking_done(self) -> None:
        """Called when the agent produces first output."""
        ...

    def on_tool_use(self, tool_name: str, details: str) -> None:
        """Called when the agent uses a tool."""
        ...

    def on_text(self, text: str) -> None:
        """Called when the agent outputs text (excluding idascript blocks)."""
        ...

    def on_script_start(self) -> None:
        """Called before executing an idascript."""
        ...

    def on_script_output(self, output: str) -> None:
        """Called with the output of an executed idascript."""
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
    Output is delegated to the callback for presentation.
    """

    def __init__(
        self,
        db,
        callback: ChatCallback,
        script_executor: Callable[[str], str] | None = None,
        verbose: bool = False,
    ):
        """Initialize the chat core.

        Args:
            db: An open ida_domain Database instance.
            callback: Handler for output events.
            script_executor: Optional custom script executor. If None, uses
                default direct execution. Plugin can inject a thread-safe
                executor that runs on the main thread.
            verbose: If True, report additional stats.
        """
        self.db = db
        self.callback = callback
        self.verbose = verbose
        self.client: ClaudeSDKClient | None = None
        # Use injected executor or default to direct execution
        self._execute_script = script_executor or self._default_execute_script

    async def connect(self) -> None:
        """Initialize and connect the Agent SDK client."""
        options = ClaudeAgentOptions(
            cwd=str(IDA_DOMAIN_SKILL_DIR),
            setting_sources=["project"],
            allowed_tools=["Read", "Glob", "Grep"],
            permission_mode="bypassPermissions",
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": SYSTEM_PROMPT_APPEND,
            },
        )

        self.client = ClaudeSDKClient(options=options)
        await self.client.connect()

    async def disconnect(self) -> None:
        """Disconnect the Agent SDK client."""
        if self.client:
            await self.client.disconnect()
            self.client = None

    def _default_execute_script(self, code: str) -> str:
        """Default script executor - direct execution.

        Args:
            code: Python code to execute with `db` in scope.

        Returns:
            Captured stdout output or error message.
        """
        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()

        try:
            exec(code, {"db": self.db, "print": print})
            return captured.getvalue()
        except Exception as e:
            return f"Script error: {e}"
        finally:
            sys.stdout = old_stdout

    async def process_message(self, user_input: str) -> str:
        """Send message to agent, process response, execute scripts.

        Args:
            user_input: The user's message/query.

        Returns:
            Combined script outputs as a string.
        """
        if not self.client:
            raise RuntimeError("Client not connected. Call connect() first.")

        self.callback.on_thinking()

        await self.client.query(user_input)

        full_text: list[str] = []
        script_outputs: list[str] = []
        first_output = True

        async for message in self.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    # Notify thinking done on first output
                    if first_output:
                        self.callback.on_thinking_done()
                        first_output = False

                    if isinstance(block, ToolUseBlock):
                        # Extract tool details
                        details = ""
                        if block.name == "Read":
                            details = block.input.get("file_path", "")
                        elif block.name == "Grep":
                            details = block.input.get("pattern", "")
                        elif block.name == "Glob":
                            details = block.input.get("pattern", "")
                        self.callback.on_tool_use(block.name, details)

                    elif isinstance(block, TextBlock):
                        text = block.text
                        full_text.append(text)

                        # Output text excluding <idascript> blocks
                        cleaned = IDASCRIPT_PATTERN.sub("", text).strip()
                        if cleaned:
                            self.callback.on_text(cleaned)

            elif isinstance(message, ResultMessage):
                # Execute any scripts found in the response
                if full_text:
                    combined = "".join(full_text)
                    matches = IDASCRIPT_PATTERN.findall(combined)
                    for script_code in matches:
                        self.callback.on_script_start()
                        output = self._execute_script(script_code.strip())
                        if output:
                            script_outputs.append(output)
                            self.callback.on_script_output(output)

                if self.verbose:
                    self.callback.on_result(
                        message.num_turns,
                        message.total_cost_usd
                    )

        return "\n".join(script_outputs) if script_outputs else ""
