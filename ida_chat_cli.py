#!/usr/bin/env python3
"""
IDA Chat CLI - Chat interface for IDA Pro using Claude Agent SDK.

Usage:
    uv run python idachat.py <binary.i64>              # Interactive mode
    uv run python idachat.py <binary.i64> -p "prompt"  # Single prompt mode
"""

import argparse
import asyncio
import re
import sys
from io import StringIO
from pathlib import Path

from claude_agent_sdk import (
    ClaudeSDKClient,
    ClaudeAgentOptions,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
    ResultMessage,
)
from ida_domain import Database


# ANSI colors for terminal output
class Colors:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    DIM = "\033[2m"
    RESET = "\033[0m"


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


class IDAChat:
    """Chat interface for IDA Pro backed by Claude Agent SDK."""

    def __init__(self, binary_path: str, verbose: bool = False):
        self.binary_path = Path(binary_path).resolve()
        self.verbose = verbose
        self.db = None
        self.client = None

    async def start(self):
        """Open database and initialize the agent client."""
        print(f"Opening database: {self.binary_path}")
        self.db = Database.open(str(self.binary_path))
        print(f"Database opened: {self.db.module}")
        print(f"Architecture: {self.db.architecture} {self.db.bitness}-bit")
        print(f"Functions: {len(self.db.functions)}")
        print()

        # Configure agent options
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

    async def stop(self):
        """Clean up resources."""
        if self.client:
            await self.client.disconnect()
        # Note: ida_domain Database doesn't need explicit close in context manager usage
        # but we opened it directly, so we leave it (it will close on process exit)

    def execute_script(self, code: str) -> str:
        """Execute an idascript against the open database."""
        # Capture stdout
        old_stdout = sys.stdout
        sys.stdout = captured = StringIO()

        try:
            # Execute with db in scope
            exec(code, {"db": self.db, "print": print})
            return captured.getvalue()
        except Exception as e:
            return f"Script error: {e}"
        finally:
            sys.stdout = old_stdout

    async def process_message(self, user_input: str) -> str:
        """Send message to agent and process response."""
        # Show thinking indicator
        print(f"{Colors.DIM}[Thinking...]{Colors.RESET}", end="", flush=True)

        await self.client.query(user_input)

        full_text = []
        script_outputs = []
        first_output = True

        async for message in self.client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    # Clear thinking indicator on first output
                    if first_output:
                        print("\r" + " " * 15 + "\r", end="")
                        first_output = False

                    if isinstance(block, ToolUseBlock):
                        # Show tool being called
                        tool_info = f"{Colors.CYAN}[{block.name}]{Colors.RESET}"
                        if block.name == "Read":
                            tool_info += f" {Colors.DIM}{block.input.get('file_path', '')}{Colors.RESET}"
                        elif block.name == "Grep":
                            tool_info += f" {Colors.DIM}{block.input.get('pattern', '')}{Colors.RESET}"
                        elif block.name == "Glob":
                            tool_info += f" {Colors.DIM}{block.input.get('pattern', '')}{Colors.RESET}"
                        print(tool_info)

                    elif isinstance(block, TextBlock):
                        text = block.text
                        full_text.append(text)

                        # Print text that's not inside <idascript> tags
                        cleaned = IDASCRIPT_PATTERN.sub("", text).strip()
                        if cleaned:
                            print(cleaned)

            elif isinstance(message, ResultMessage):
                print()  # New line after streaming

                # Now check for and execute any scripts in the final text
                if full_text:
                    combined = "".join(full_text)
                    matches = IDASCRIPT_PATTERN.findall(combined)
                    for script_code in matches:
                        print(f"{Colors.YELLOW}[Executing script...]{Colors.RESET}")
                        output = self.execute_script(script_code.strip())
                        if output:
                            script_outputs.append(output)

                if self.verbose:
                    print(f"{Colors.DIM}[Turns: {message.num_turns}, Cost: ${message.total_cost_usd or 0:.4f}]{Colors.RESET}")

        # Return script output
        return "\n".join(script_outputs) if script_outputs else ""

    async def run_interactive(self):
        """Run interactive chat loop."""
        print("IDA Chat ready. Type 'exit' or 'quit' to leave.")
        print("-" * 40)

        while True:
            try:
                user_input = input("\nYou: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nGoodbye!")
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                print("Goodbye!")
                break

            result = await self.process_message(user_input)
            print(f"\n{result}")

    async def run_single_prompt(self, prompt: str):
        """Execute a single prompt and exit."""
        result = await self.process_message(prompt)
        print(result)


async def async_main():
    parser = argparse.ArgumentParser(
        description="Chat interface for IDA Pro using Claude Agent SDK"
    )
    parser.add_argument("binary", help="Path to binary or .i64 file")
    parser.add_argument("-p", "--prompt", help="Single prompt (non-interactive mode)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show agent reasoning")

    args = parser.parse_args()

    # Validate binary exists
    if not Path(args.binary).exists():
        print(f"Error: File not found: {args.binary}", file=sys.stderr)
        sys.exit(1)

    chat = IDAChat(args.binary, verbose=args.verbose)

    try:
        await chat.start()

        if args.prompt:
            await chat.run_single_prompt(args.prompt)
        else:
            await chat.run_interactive()
    finally:
        await chat.stop()


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
