#!/usr/bin/env python3
"""
IDA Chat CLI - Command-line chat interface for IDA Pro.

Usage:
    uv run python ida_chat_cli.py <binary.i64>              # Interactive mode
    uv run python ida_chat_cli.py <binary.i64> -p "prompt"  # Single prompt mode
"""

import argparse
import asyncio
import sys
from pathlib import Path

# Ensure local modules are importable
sys.path.insert(0, str(Path(__file__).parent.resolve()))

# Import local module first (before ida_domain which may modify sys.path)
from ida_chat_core import IDAChatCore, ChatCallback

from ida_domain import Database


# ANSI colors for terminal output
class Colors:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    DIM = "\033[2m"
    RESET = "\033[0m"


class CLICallback(ChatCallback):
    """Terminal output implementation of ChatCallback."""

    def on_thinking(self) -> None:
        print(f"{Colors.DIM}[Thinking...]{Colors.RESET}", end="", flush=True)

    def on_thinking_done(self) -> None:
        # Clear the thinking indicator
        print("\r" + " " * 15 + "\r", end="")

    def on_tool_use(self, tool_name: str, details: str) -> None:
        tool_info = f"{Colors.CYAN}[{tool_name}]{Colors.RESET}"
        if details:
            tool_info += f" {Colors.DIM}{details}{Colors.RESET}"
        print(tool_info)

    def on_text(self, text: str) -> None:
        print(text)

    def on_script_start(self) -> None:
        print(f"{Colors.YELLOW}[Executing script...]{Colors.RESET}")

    def on_script_output(self, output: str) -> None:
        print(output)

    def on_error(self, error: str) -> None:
        print(f"{Colors.YELLOW}Error: {error}{Colors.RESET}", file=sys.stderr)

    def on_result(self, num_turns: int, cost: float | None) -> None:
        print(f"{Colors.DIM}[Turns: {num_turns}, Cost: ${cost or 0:.4f}]{Colors.RESET}")


class IDAChat:
    """CLI chat interface for IDA Pro."""

    def __init__(self, binary_path: str, verbose: bool = False):
        self.binary_path = Path(binary_path).resolve()
        self.verbose = verbose
        self.db = None
        self.core: IDAChatCore | None = None

    async def start(self) -> None:
        """Open database and initialize the agent."""
        print(f"Opening database: {self.binary_path}")
        self.db = Database.open(str(self.binary_path))
        print(f"Database opened: {self.db.module}")
        print(f"Architecture: {self.db.architecture} {self.db.bitness}-bit")
        print(f"Functions: {len(self.db.functions)}")
        print()

        callback = CLICallback()
        self.core = IDAChatCore(self.db, callback, verbose=self.verbose)
        await self.core.connect()

    async def stop(self, save: bool = False) -> None:
        """Clean up resources."""
        if self.core:
            await self.core.disconnect()
        if self.db and save:
            print(f"{Colors.CYAN}Saving and packing database...{Colors.RESET}")
            self.db.save()
            print(f"{Colors.GREEN}Database saved.{Colors.RESET}")

    def prompt_save_on_exit(self) -> bool:
        """Ask user if they want to save the database."""
        print()
        try:
            response = input(f"{Colors.YELLOW}Save database before exiting? [y/N]: {Colors.RESET}").strip().lower()
            return response in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    async def run_interactive(self) -> bool:
        """Run interactive chat loop. Returns True if user wants to save on exit."""
        print("IDA Chat ready. Type 'exit' or 'quit' to leave. Ctrl+C to exit.")
        print("-" * 40)

        save_on_exit = False

        while True:
            try:
                user_input = input("\nYou: ").strip()
            except EOFError:
                print("\nGoodbye!")
                break
            except KeyboardInterrupt:
                save_on_exit = self.prompt_save_on_exit()
                print("Goodbye!")
                break

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                save_on_exit = self.prompt_save_on_exit()
                print("Goodbye!")
                break

            try:
                await self.core.process_message(user_input)
                print()  # Blank line after response
            except KeyboardInterrupt:
                print(f"\n{Colors.YELLOW}[Interrupted]{Colors.RESET}")
                save_on_exit = self.prompt_save_on_exit()
                print("Goodbye!")
                break

        return save_on_exit

    async def run_single_prompt(self, prompt: str) -> None:
        """Execute a single prompt and exit."""
        await self.core.process_message(prompt)


async def async_main():
    parser = argparse.ArgumentParser(
        description="Chat interface for IDA Pro using Claude Agent SDK"
    )
    parser.add_argument("binary", help="Path to binary or .i64 file")
    parser.add_argument("-p", "--prompt", help="Single prompt (non-interactive mode)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Show agent stats")

    args = parser.parse_args()

    if not Path(args.binary).exists():
        print(f"Error: File not found: {args.binary}", file=sys.stderr)
        sys.exit(1)

    chat = IDAChat(args.binary, verbose=args.verbose)
    save_on_exit = False

    try:
        await chat.start()

        if args.prompt:
            await chat.run_single_prompt(args.prompt)
        else:
            save_on_exit = await chat.run_interactive()
    except KeyboardInterrupt:
        save_on_exit = chat.prompt_save_on_exit() if chat.db else False
        print("Goodbye!")
    finally:
        await chat.stop(save=save_on_exit)


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
