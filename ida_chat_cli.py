#!/usr/bin/env python3
"""
IDA Chat CLI - Command-line chat interface for IDA Pro.

Usage:
    ida-chat <binary.i64>              # Interactive mode
    ida-chat <binary.i64> -p "prompt"  # Single prompt mode
    ida-chat transcript                # Generate HTML transcript from sessions
"""

import argparse
import asyncio
import sys
import tempfile
import webbrowser
from pathlib import Path

# Ensure local modules are importable
sys.path.insert(0, str(Path(__file__).parent.resolve()))

# Import local module first (before ida_domain which may modify sys.path)
from ida_chat_core import IDAChatCore, ChatCallback
from ida_chat_history import MessageHistory

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

    def on_turn_start(self, turn: int, max_turns: int) -> None:
        pass  # Don't display turn info in UI

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

    def on_script_code(self, code: str) -> None:
        # Show script code with syntax highlighting hint
        print(f"{Colors.YELLOW}[Executing script]{Colors.RESET}")
        # Show first few lines of the script
        lines = code.strip().split('\n')
        preview = '\n'.join(lines[:5])
        if len(lines) > 5:
            preview += f"\n{Colors.DIM}... ({len(lines) - 5} more lines){Colors.RESET}"
        print(f"{Colors.DIM}{preview}{Colors.RESET}")

    def on_script_output(self, output: str) -> None:
        print(f"{Colors.GREEN}{output}{Colors.RESET}")

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


def run_transcript_command(args: list[str]) -> int:
    """Run the transcript subcommand to generate HTML from sessions."""
    from datetime import datetime

    from ida_chat_core import export_transcript_to_dir

    parser = argparse.ArgumentParser(
        prog="ida-chat transcript",
        description="Generate HTML transcript from IDA Chat sessions"
    )
    parser.add_argument(
        "session",
        nargs="?",
        help="Session file path or session ID (interactive picker if omitted)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output directory (uses temp dir and opens browser if omitted)"
    )
    parser.add_argument(
        "-l", "--list",
        action="store_true",
        help="List all available sessions"
    )
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Don't open browser after generating"
    )

    parsed = parser.parse_args(args)
    sessions_base = MessageHistory.BASE_DIR

    # Gather all sessions across all binaries
    all_sessions: list[tuple[Path, str, str, int, str]] = []  # (path, binary, timestamp, count, first_msg)

    if sessions_base.exists():
        for binary_dir in sessions_base.iterdir():
            if not binary_dir.is_dir():
                continue
            for session_file in binary_dir.glob("*.jsonl"):
                # Get first timestamp and message count
                first_ts = None
                count = 0
                first_msg = None
                try:
                    with open(session_file, "r") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            import json
                            entry = json.loads(line)
                            count += 1
                            if first_ts is None:
                                first_ts = entry.get("timestamp", "")
                            if first_msg is None and entry.get("type") == "user":
                                msg = entry.get("message", {})
                                content = msg.get("content", [])
                                if isinstance(content, list):
                                    for item in content:
                                        if isinstance(item, dict) and item.get("type") == "text":
                                            first_msg = item.get("text", "")[:60]
                                            break
                except Exception:
                    continue

                all_sessions.append((
                    session_file,
                    binary_dir.name,
                    first_ts or "",
                    count,
                    first_msg or "(empty)"
                ))

    # Sort by timestamp, most recent first
    all_sessions.sort(key=lambda x: x[2], reverse=True)

    if not all_sessions:
        print("No sessions found in ~/.ida-chat/sessions/", file=sys.stderr)
        return 1

    # List mode
    if parsed.list:
        print(f"{'Date':<20} {'Messages':>8}  {'Binary':<30} {'First message'}")
        print("-" * 100)
        for path, binary, ts, count, first_msg in all_sessions:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_str = ts[:19] if ts else "N/A"
            binary_short = binary[:28] + ".." if len(binary) > 30 else binary
            msg_short = first_msg[:40] + "..." if len(first_msg) > 40 else first_msg
            print(f"{date_str:<20} {count:>8}  {binary_short:<30} {msg_short}")
        return 0

    # Find session file
    session_file: Path | None = None

    if parsed.session:
        # Direct path or search by ID
        session_path = Path(parsed.session)
        if session_path.exists():
            session_file = session_path
        else:
            # Search by session ID
            for path, _, _, _, _ in all_sessions:
                if path.stem == parsed.session or parsed.session in str(path):
                    session_file = path
                    break
            if not session_file:
                print(f"Session not found: {parsed.session}", file=sys.stderr)
                return 1
    else:
        # Interactive picker
        print("Select a session:")
        print()
        for i, (path, binary, ts, count, first_msg) in enumerate(all_sessions[:20], 1):
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                date_str = dt.strftime("%Y-%m-%d %H:%M")
            except Exception:
                date_str = ts[:19] if ts else "N/A"
            binary_short = binary[:20] + ".." if len(binary) > 22 else binary
            msg_short = first_msg[:35] + "..." if len(first_msg) > 35 else first_msg
            print(f"  {i:>2}. {date_str}  {count:>3} msgs  {binary_short:<22} {msg_short}")

        print()
        try:
            choice = input("Enter number (or q to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0

        if choice.lower() in ("q", "quit", ""):
            return 0

        try:
            idx = int(choice) - 1
            if 0 <= idx < len(all_sessions):
                session_file = all_sessions[idx][0]
            else:
                print("Invalid selection", file=sys.stderr)
                return 1
        except ValueError:
            print("Invalid number", file=sys.stderr)
            return 1

    # Generate HTML
    if parsed.output:
        output_dir = Path(parsed.output)
    else:
        output_dir = Path(tempfile.gettempdir()) / f"ida-chat-{session_file.stem}"

    print(f"Generating transcript from: {session_file}")
    print(f"Output directory: {output_dir}")

    index_html = export_transcript_to_dir(session_file, output_dir)

    if not parsed.no_open and not parsed.output:
        index_url = index_html.resolve().as_uri()
        print(f"Opening: {index_url}")
        webbrowser.open(index_url)

    return 0


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
    # Handle transcript subcommand before main argparse
    if len(sys.argv) > 1 and sys.argv[1] == "transcript":
        sys.exit(run_transcript_command(sys.argv[2:]))

    asyncio.run(async_main())


if __name__ == "__main__":
    main()
