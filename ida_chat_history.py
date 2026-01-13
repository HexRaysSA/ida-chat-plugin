"""
IDA Chat History - Persistent message history storage.

Stores conversation history in JSONL format compatible with Claude Code's approach.
Each binary gets its own session directory, with individual session files.

Format is compatible with claude-code-log for viewing transcripts.
"""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class MessageHistory:
    """Manages persistent message history for IDA Chat sessions.

    Storage structure:
        $HOME/.ida-chat/sessions/{encoded_binary_path}/{session-uuid}.jsonl

    Each line in a session file is a JSON object representing a message,
    compatible with Claude Code's JSONL format for use with claude-code-log.
    """

    BASE_DIR = Path.home() / ".ida-chat" / "sessions"
    VERSION = "ida-chat-1.0.0"

    def __init__(self, binary_path: str):
        """Initialize message history for a binary.

        Args:
            binary_path: Full path to the binary being analyzed.
        """
        self.binary_path = binary_path
        self.session_dir = self._get_session_dir()
        self.session_id: str | None = None
        self.session_file: Path | None = None
        self._parent_uuid: str | None = None

    def _encode_path(self, path: str) -> str:
        """Encode a file path for use as a directory name.

        Replaces / and spaces with underscores, similar to Claude Code's approach.

        Args:
            path: The file path to encode.

        Returns:
            Encoded path safe for use as directory name.
        """
        # Replace path separators and spaces with underscores
        encoded = re.sub(r'[/\\: ]', '_', path)
        # Remove any leading underscores for cleaner names
        encoded = encoded.lstrip('_')
        # Collapse multiple underscores
        encoded = re.sub(r'_+', '_', encoded)
        return encoded

    def _get_session_dir(self) -> Path:
        """Get the session directory for the current binary.

        Returns:
            Path to the session directory.
        """
        encoded_path = self._encode_path(self.binary_path)
        return self.BASE_DIR / encoded_path

    def start_new_session(self) -> str:
        """Start a new session and create the session file.

        Returns:
            The new session ID (UUID).
        """
        self.session_id = str(uuid.uuid4())
        self._parent_uuid = None

        # Create session directory if it doesn't exist
        self.session_dir.mkdir(parents=True, exist_ok=True)

        # Create session file
        self.session_file = self.session_dir / f"{self.session_id}.jsonl"

        return self.session_id

    def _generate_uuid(self) -> str:
        """Generate a unique message UUID."""
        return str(uuid.uuid4())

    def _get_timestamp(self) -> str:
        """Get current UTC timestamp in ISO format."""
        return datetime.now(timezone.utc).isoformat()

    def _create_base_entry(self) -> dict[str, Any]:
        """Create base entry with common fields matching Claude Code format."""
        msg_uuid = self._generate_uuid()
        entry = {
            "uuid": msg_uuid,
            "parentUuid": self._parent_uuid,
            "sessionId": self.session_id,
            "timestamp": self._get_timestamp(),
            "version": self.VERSION,
            "cwd": str(Path(self.binary_path).parent),
            "isSidechain": False,
            "userType": "external",
        }
        return entry

    def append_user_message(self, content: str) -> str:
        """Append a user message to the session.

        Args:
            content: The user's message text.

        Returns:
            The UUID of the appended message.
        """
        if not self.session_file:
            raise RuntimeError("No active session. Call start_new_session() first.")

        entry = self._create_base_entry()
        entry["type"] = "user"
        entry["message"] = {
            "role": "user",
            "content": [{"type": "text", "text": content}]
        }

        self._write_entry(entry)
        return entry["uuid"]

    def append_assistant_message(
        self,
        content: str,
        model: str = "claude-sonnet-4-20250514",
        usage: dict[str, Any] | None = None
    ) -> str:
        """Append an assistant message to the session.

        Args:
            content: The assistant's response text.
            model: The model name used.
            usage: Optional token usage information.

        Returns:
            The UUID of the appended message.
        """
        if not self.session_file:
            raise RuntimeError("No active session. Call start_new_session() first.")

        entry = self._create_base_entry()
        entry["type"] = "assistant"
        entry["message"] = {
            "id": f"msg_{self._generate_uuid()}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": content}],
            "stop_reason": "end_turn",
        }
        if usage:
            entry["message"]["usage"] = usage

        self._write_entry(entry)
        return entry["uuid"]

    def append_tool_use(
        self,
        tool_name: str,
        tool_input: dict[str, Any],
        tool_use_id: str | None = None
    ) -> str:
        """Append a tool use message to the session.

        Args:
            tool_name: Name of the tool being used.
            tool_input: Input parameters for the tool.
            tool_use_id: Optional tool use ID (generated if not provided).

        Returns:
            The UUID of the appended message.
        """
        if not self.session_file:
            raise RuntimeError("No active session. Call start_new_session() first.")

        if tool_use_id is None:
            tool_use_id = f"toolu_{self._generate_uuid()}"

        entry = self._create_base_entry()
        entry["type"] = "assistant"
        entry["message"] = {
            "id": f"msg_{self._generate_uuid()}",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-20250514",
            "content": [{
                "type": "tool_use",
                "id": tool_use_id,
                "name": tool_name,
                "input": tool_input
            }],
            "stop_reason": "tool_use",
        }

        self._write_entry(entry)
        return entry["uuid"]

    def append_tool_result(
        self,
        tool_use_id: str,
        result: str | list[dict[str, Any]],
        is_error: bool = False
    ) -> str:
        """Append a tool result message to the session.

        Args:
            tool_use_id: The ID of the tool use this result corresponds to.
            result: The tool result (string or list of content items).
            is_error: Whether the result is an error.

        Returns:
            The UUID of the appended message.
        """
        if not self.session_file:
            raise RuntimeError("No active session. Call start_new_session() first.")

        # Convert string result to content list format
        if isinstance(result, str):
            content = result
        else:
            content = result

        entry = self._create_base_entry()
        entry["type"] = "user"
        entry["message"] = {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": content,
                "is_error": is_error
            }]
        }

        self._write_entry(entry)
        return entry["uuid"]

    def append_thinking(self, thinking: str) -> str:
        """Append a thinking block to the session.

        Args:
            thinking: The thinking/reasoning text.

        Returns:
            The UUID of the appended message.
        """
        if not self.session_file:
            raise RuntimeError("No active session. Call start_new_session() first.")

        entry = self._create_base_entry()
        entry["type"] = "assistant"
        entry["message"] = {
            "id": f"msg_{self._generate_uuid()}",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-20250514",
            "content": [{
                "type": "thinking",
                "thinking": thinking
            }],
        }

        self._write_entry(entry)
        return entry["uuid"]

    def append_system_message(
        self,
        content: str,
        level: str = "info",
        subtype: str | None = None
    ) -> str:
        """Append a system message to the session.

        Args:
            content: The system message content.
            level: Message level (info, warning, error).
            subtype: Optional subtype for the system message.

        Returns:
            The UUID of the appended message.
        """
        if not self.session_file:
            raise RuntimeError("No active session. Call start_new_session() first.")

        entry = self._create_base_entry()
        entry["type"] = "system"
        entry["content"] = content
        entry["level"] = level
        if subtype:
            entry["subtype"] = subtype

        self._write_entry(entry)
        return entry["uuid"]

    def append_script_execution(
        self,
        code: str,
        output: str,
        is_error: bool = False
    ) -> str:
        """Append a script execution (tool use + result) to the session.

        This is a convenience method that creates both tool_use and tool_result
        entries for IDA script execution.

        Args:
            code: The Python code that was executed.
            output: The output from execution.
            is_error: Whether the execution resulted in an error.

        Returns:
            The UUID of the tool result message.
        """
        tool_use_id = f"toolu_{self._generate_uuid()}"

        # First, append the tool use
        self.append_tool_use(
            tool_name="IDAPythonExec",
            tool_input={"code": code},
            tool_use_id=tool_use_id
        )

        # Then append the result
        return self.append_tool_result(
            tool_use_id=tool_use_id,
            result=output,
            is_error=is_error
        )

    def _write_entry(self, entry: dict[str, Any]) -> None:
        """Write an entry to the session file and update parent UUID.

        Args:
            entry: The entry to write.
        """
        if not self.session_file:
            raise RuntimeError("No active session. Call start_new_session() first.")

        with open(self.session_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Update parent UUID for chaining
        self._parent_uuid = entry["uuid"]

    def load_session(self, session_id: str) -> list[dict[str, Any]]:
        """Load all messages from a session.

        Args:
            session_id: The session UUID to load.

        Returns:
            List of message dictionaries.
        """
        session_file = self.session_dir / f"{session_id}.jsonl"

        if not session_file.exists():
            return []

        messages = []
        with open(session_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        messages.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        return messages

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all sessions for the current binary.

        Returns:
            List of session summaries with id, first_message, timestamp, message_count.
        """
        if not self.session_dir.exists():
            return []

        sessions = []
        for session_file in sorted(self.session_dir.glob("*.jsonl"), reverse=True):
            session_id = session_file.stem

            # Read first user message and count total messages
            first_user_message = None
            first_timestamp = None
            message_count = 0

            with open(session_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        message_count += 1

                        if first_timestamp is None:
                            first_timestamp = entry.get("timestamp")

                        if first_user_message is None and entry.get("type") == "user":
                            msg = entry.get("message", {})
                            content = msg.get("content", [])
                            if content and isinstance(content, list):
                                for item in content:
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        first_user_message = item.get("text", "")[:100]
                                        break
                            elif isinstance(content, str):
                                first_user_message = content[:100]
                    except json.JSONDecodeError:
                        continue

            sessions.append({
                "id": session_id,
                "first_message": first_user_message or "(empty)",
                "timestamp": first_timestamp,
                "message_count": message_count,
            })

        return sessions

    def get_current_session_id(self) -> str | None:
        """Get the current session ID."""
        return self.session_id

    def get_all_user_messages(self) -> list[str]:
        """Get all user messages from all sessions for this binary.

        Messages are returned in chronological order (oldest first),
        suitable for up-arrow history navigation.

        Returns:
            List of user message content strings.
        """
        if not self.session_dir.exists():
            return []

        # Collect all user messages with timestamps
        messages_with_time: list[tuple[str, str]] = []

        for session_file in self.session_dir.glob("*.jsonl"):
            with open(session_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("type") == "user":
                            msg = entry.get("message", {})
                            content = msg.get("content", [])
                            timestamp = entry.get("timestamp", "")

                            # Extract text from content (handle both formats)
                            text = None
                            if isinstance(content, list):
                                for item in content:
                                    if isinstance(item, dict) and item.get("type") == "text":
                                        text = item.get("text", "")
                                        break
                            elif isinstance(content, str):
                                text = content

                            if text:
                                messages_with_time.append((timestamp, text))
                    except json.JSONDecodeError:
                        continue

        # Sort by timestamp (chronological order)
        messages_with_time.sort(key=lambda x: x[0])

        # Return just the content, deduplicated while preserving order
        seen: set[str] = set()
        unique_messages: list[str] = []
        for _, content in messages_with_time:
            if content not in seen:
                seen.add(content)
                unique_messages.append(content)

        return unique_messages
