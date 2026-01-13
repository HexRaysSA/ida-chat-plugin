"""
IDA Chat Core - Shared foundation for CLI and Plugin.

This module contains the common Agent SDK integration, script execution,
and message processing used by both the CLI and IDA plugin.
"""

import logging
import re
import sys
from io import StringIO
from pathlib import Path
from typing import Callable, Protocol

# Set up debug logging to file
LOG_FILE = Path("/tmp/ida-chat.log")
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, mode="a"),
    ]
)
logger = logging.getLogger("ida-chat")

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ResultMessage,
)


# Project directory for agent SDK (plugin install directory)
PROJECT_DIR = Path(__file__).parent.resolve()

# Regex to extract <idascript>...</idascript> blocks
IDASCRIPT_PATTERN = re.compile(r"<idascript>(.*?)</idascript>", re.DOTALL)

# Prompt file location (relative to plugin directory, not work dir)
PROMPT_FILE = Path(__file__).parent.resolve() / "PROMPT.md"


def _load_system_prompt() -> str:
    """Load the system prompt from PROMPT.md."""
    if PROMPT_FILE.exists():
        return PROMPT_FILE.read_text()
    # Fallback if file not found
    logger.warning(f"PROMPT.md not found at {PROMPT_FILE}")
    return "You have access to an open IDA database via the `db` variable. Use <idascript> tags for code."


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

    def on_tool_use(self, tool_name: str, details: str) -> None:
        """Called when the agent uses a tool (Read, Glob, Grep, Skill)."""
        ...

    def on_text(self, text: str) -> None:
        """Called when the agent outputs text (excluding idascript blocks)."""
        ...

    def on_script_code(self, code: str) -> None:
        """Called with the script code before execution."""
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
    Implements an agentic loop that feeds script results back to the agent.
    Output is delegated to the callback for presentation.
    """

    def __init__(
        self,
        db,
        callback: ChatCallback,
        script_executor: Callable[[str], str] | None = None,
        verbose: bool = False,
        max_turns: int = 20,
    ):
        """Initialize the chat core.

        Args:
            db: An open ida_domain Database instance.
            callback: Handler for output events.
            script_executor: Optional custom script executor. If None, uses
                default direct execution. Plugin can inject a thread-safe
                executor that runs on the main thread.
            verbose: If True, report additional stats.
            max_turns: Maximum agentic turns before stopping (default 20).
        """
        self.db = db
        self.callback = callback
        self.verbose = verbose
        self.max_turns = max_turns
        self.client: ClaudeSDKClient | None = None
        # Use injected executor or default to direct execution
        self._execute_script = script_executor or self._default_execute_script

    async def connect(self) -> None:
        """Initialize and connect the Agent SDK client."""
        logger.info("=" * 60)
        logger.info("Connecting to Claude Agent SDK")
        logger.info(f"CWD: {PROJECT_DIR}")

        options = ClaudeAgentOptions(
            cwd=str(PROJECT_DIR),
            setting_sources=["project"],
            allowed_tools=["Read", "Glob", "Grep", "Task"],
            permission_mode="bypassPermissions",
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": _load_system_prompt(),
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

    async def _process_single_response(self) -> tuple[list[str], list[str]]:
        """Process a single agent response.

        Returns:
            Tuple of (scripts_found, script_outputs)
        """
        full_text: list[str] = []
        scripts_found: list[str] = []
        script_outputs: list[str] = []
        first_output = True

        async for message in self.client.receive_response():
            logger.debug(f"Received message type: {type(message).__name__}")

            if isinstance(message, AssistantMessage):
                logger.debug(f"AssistantMessage with {len(message.content)} blocks")
                for i, block in enumerate(message.content):
                    logger.debug(f"  Block {i}: {type(block).__name__}")

                    # Notify thinking done on first output
                    if first_output:
                        self.callback.on_thinking_done()
                        first_output = False

                    if isinstance(block, ToolUseBlock):
                        logger.info(f"TOOL USE: {block.name}")
                        logger.debug(f"  Tool input: {block.input}")

                        # Extract tool details based on tool type
                        details = ""
                        if block.name == "Read":
                            details = block.input.get("file_path", "")
                        elif block.name == "Grep":
                            details = block.input.get("pattern", "")
                        elif block.name == "Glob":
                            details = block.input.get("pattern", "")
                        elif block.name == "Task":
                            details = block.input.get("description", "")
                        else:
                            # Log unknown tools
                            logger.warning(f"  Unknown tool: {block.name}, input: {block.input}")
                            details = str(block.input)
                        self.callback.on_tool_use(block.name, details)

                    elif isinstance(block, TextBlock):
                        text = block.text
                        logger.debug(f"  TextBlock ({len(text)} chars): {text[:100]}...")
                        full_text.append(text)

                        # Output text excluding <idascript> blocks
                        cleaned = IDASCRIPT_PATTERN.sub("", text).strip()
                        if cleaned:
                            self.callback.on_text(cleaned)
                    else:
                        logger.warning(f"  Unknown block type: {type(block).__name__}")

            elif isinstance(message, ResultMessage):
                logger.info(f"ResultMessage: turns={message.num_turns}, cost={message.total_cost_usd}")

                # Extract scripts from the response
                if full_text:
                    combined = "".join(full_text)
                    scripts_found = IDASCRIPT_PATTERN.findall(combined)
                    logger.info(f"Found {len(scripts_found)} scripts in response")

                    # Execute each script
                    for j, script_code in enumerate(scripts_found):
                        code = script_code.strip()
                        logger.debug(f"Script {j+1}:\n{code}")
                        self.callback.on_script_code(code)
                        output = self._execute_script(code)
                        logger.debug(f"Script {j+1} output:\n{output}")
                        script_outputs.append(output)
                        if output:
                            self.callback.on_script_output(output)

                if self.verbose:
                    self.callback.on_result(
                        message.num_turns,
                        message.total_cost_usd
                    )
            else:
                logger.warning(f"Unknown message type: {type(message).__name__}")

        return scripts_found, script_outputs

    async def process_message(self, user_input: str) -> str:
        """Agentic loop - process message and continue until agent is done.

        The agent will keep working, seeing script outputs and fixing errors,
        until either:
        - It responds without any <idascript> tags (task complete)
        - Maximum turns is reached

        Args:
            user_input: The user's message/query.

        Returns:
            Combined script outputs as a string.
        """
        if not self.client:
            raise RuntimeError("Client not connected. Call connect() first.")

        logger.info("-" * 60)
        logger.info(f"USER MESSAGE: {user_input[:200]}...")

        current_input = user_input
        all_script_outputs: list[str] = []
        turn = 0

        while turn < self.max_turns:
            turn += 1
            logger.info(f"=== TURN {turn}/{self.max_turns} ===")
            self.callback.on_turn_start(turn, self.max_turns)
            self.callback.on_thinking()

            # Send message to agent
            logger.debug(f"Sending to agent: {current_input[:200]}...")
            await self.client.query(current_input)

            # Process response and execute any scripts
            scripts_found, script_outputs = await self._process_single_response()
            all_script_outputs.extend(script_outputs)

            if not scripts_found:
                # No scripts in response = agent is done
                logger.info("No scripts in response - agent is done")
                break

            # Feed script results back to agent for next turn
            if script_outputs:
                # Format all outputs for the agent
                formatted_outputs = []
                for i, output in enumerate(script_outputs, 1):
                    if len(scripts_found) > 1:
                        formatted_outputs.append(f"Script {i} output:\n{output}")
                    else:
                        formatted_outputs.append(output)
                current_input = "Script output:\n\n" + "\n\n".join(formatted_outputs)
                logger.debug(f"Feeding back to agent: {current_input[:200]}...")
            else:
                current_input = "Script executed successfully with no output."
                logger.debug("Script had no output, notifying agent")

        if turn >= self.max_turns:
            logger.warning(f"Reached maximum turns ({self.max_turns})")
            self.callback.on_error(f"Reached maximum turns ({self.max_turns})")

        return "\n".join(all_script_outputs) if all_script_outputs else ""
