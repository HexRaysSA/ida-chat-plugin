"""
IDA Chat - LLM Chat Client Plugin for IDA Pro

A dockable chat interface powered by Claude Agent SDK for
AI-assisted reverse engineering within IDA Pro.
"""

import asyncio
import os
import re
import sys
from io import StringIO

# Signal to core that we're running inside IDA Pro (enables UI interaction API)
os.environ["IDA_CHAT_INSIDE_IDA"] = "1"
from pathlib import Path
from typing import Callable

import ida_idaapi
import ida_kernwin
import ida_settings
from ida_domain import Database
from PySide6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
    QScrollArea,
    QFrame,
    QSizePolicy,
    QPlainTextEdit,
    QApplication,
    QRadioButton,
    QButtonGroup,
    QLineEdit,
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, QTimer
from PySide6.QtGui import QKeyEvent, QPalette, QFont, QPixmap

# Ensure local modules are importable
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from ida_chat_core import IDAChatCore, ChatCallback, test_claude_connection
from ida_chat_history import MessageHistory


# Plugin metadata
PLUGIN_NAME = "IDA Chat"
PLUGIN_COMMENT = "LLM Chat Client for IDA Pro"
PLUGIN_HELP = "A chat interface for interacting with LLMs from within IDA Pro"

# Action configuration
ACTION_ID = "ida_chat:toggle_widget"
ACTION_NAME = "Show IDA Chat"
ACTION_HOTKEY = "Ctrl+Shift+C"
ACTION_TOOLTIP = "Toggle the IDA Chat panel"

# Widget form title
WIDGET_TITLE = "IDA Chat"


def get_ida_colors():
    """Get colors from IDA's current palette."""
    app = QApplication.instance()
    palette = app.palette()

    return {
        "window": palette.color(QPalette.Window).name(),
        "window_text": palette.color(QPalette.WindowText).name(),
        "base": palette.color(QPalette.Base).name(),
        "alt_base": palette.color(QPalette.AlternateBase).name(),
        "text": palette.color(QPalette.Text).name(),
        "button": palette.color(QPalette.Button).name(),
        "button_text": palette.color(QPalette.ButtonText).name(),
        "highlight": palette.color(QPalette.Highlight).name(),
        "highlight_text": palette.color(QPalette.HighlightedText).name(),
        "mid": palette.color(QPalette.Mid).name(),
        "dark": palette.color(QPalette.Dark).name(),
        "light": palette.color(QPalette.Light).name(),
    }


# -----------------------------------------------------------------------------
# Settings Management (using ida-settings)
# -----------------------------------------------------------------------------


def get_show_wizard() -> bool:
    """Returns whether to show the setup wizard."""
    if ida_settings.has_current_plugin_setting("show_wizard"):
        return ida_settings.get_current_plugin_setting("show_wizard")
    return True  # Default to true


def set_show_wizard(value: bool) -> None:
    """Set whether to show the setup wizard."""
    ida_settings.set_current_plugin_setting("show_wizard", value)


def get_auth_type() -> str | None:
    """Returns 'system', 'oauth', or 'api_key', or None if not configured."""
    if ida_settings.has_current_plugin_setting("auth_type"):
        return ida_settings.get_current_plugin_setting("auth_type")
    return None


def get_api_key() -> str | None:
    """Returns the stored API key/token."""
    if ida_settings.has_current_plugin_setting("api_key"):
        return ida_settings.get_current_plugin_setting("api_key")
    return None


def save_auth_settings(auth_type: str, api_key: str | None = None) -> None:
    """Store authentication settings and disable wizard."""
    ida_settings.set_current_plugin_setting("auth_type", auth_type)
    if api_key:
        ida_settings.set_current_plugin_setting("api_key", api_key)
    elif ida_settings.has_current_plugin_setting("api_key"):
        ida_settings.del_current_plugin_setting("api_key")
    # Disable wizard after saving settings
    set_show_wizard(False)


def apply_auth_to_environment() -> None:
    """Set environment variables based on stored settings."""
    auth_type = get_auth_type()
    api_key = get_api_key()
    if auth_type == "system":
        pass  # Use existing system configuration (keychain, env vars)
    elif auth_type == "oauth" and api_key:
        os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = api_key
    elif auth_type == "api_key" and api_key:
        os.environ["ANTHROPIC_API_KEY"] = api_key


class CollapsibleSection(QFrame):
    """Expandable/collapsible section for long content."""

    # Threshold for collapsing (lines)
    COLLAPSE_THRESHOLD = 10

    def __init__(self, title: str, content: str, collapsed: bool = True, parent=None):
        super().__init__(parent)
        self._collapsed = collapsed
        self._title = title
        self._content = content
        self._setup_ui()

    def _setup_ui(self):
        colors = get_ida_colors()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)

        # Header with toggle button
        self.header = QPushButton()
        self._update_header_text()
        self.header.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {colors['mid']};
                border: none;
                text-align: left;
                padding: 2px 4px;
                font-size: 11px;
            }}
            QPushButton:hover {{
                color: {colors['text']};
            }}
        """)
        self.header.clicked.connect(self._toggle)
        layout.addWidget(self.header)

        # Content area
        self.content_label = QLabel()
        self.content_label.setTextFormat(Qt.RichText)
        self.content_label.setWordWrap(True)
        self.content_label.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        self.content_label.setStyleSheet(f"""
            QLabel {{
                background-color: {colors['alt_base']};
                color: {colors['text']};
                padding: 8px;
                border-radius: 4px;
                font-family: monospace;
                font-size: 11px;
            }}
        """)
        self._update_content()
        layout.addWidget(self.content_label)

    def _update_header_text(self):
        arrow = "▶" if self._collapsed else "▼"
        line_count = len(self._content.strip().split('\n'))
        self.header.setText(f"{arrow} {self._title} ({line_count} lines)")

    def _update_content(self):
        if self._collapsed:
            # Show first few lines with ellipsis
            lines = self._content.strip().split('\n')
            preview = '\n'.join(lines[:3])
            if len(lines) > 3:
                preview += f"\n... ({len(lines) - 3} more lines)"
            self.content_label.setText(f"<pre>{preview}</pre>")
        else:
            self.content_label.setText(f"<pre>{self._content}</pre>")

    def _toggle(self):
        self._collapsed = not self._collapsed
        self._update_header_text()
        self._update_content()

    @staticmethod
    def should_collapse(content: str) -> bool:
        """Check if content should be collapsed."""
        return len(content.strip().split('\n')) > CollapsibleSection.COLLAPSE_THRESHOLD


def markdown_to_html(text: str) -> str:
    """Convert markdown to HTML for display in QLabel with rich text."""
    import html

    # Get theme-aware colors
    colors = get_ida_colors()
    code_bg = colors['dark']
    code_fg = colors['text']

    # Escape HTML first
    text = html.escape(text)

    # Code blocks (``` ... ```) - must be before inline code
    def replace_code_block(match):
        code = match.group(1)
        return f'<pre style="background-color: {code_bg}; color: {code_fg}; padding: 8px; border-radius: 4px; overflow-x: auto;"><code>{code}</code></pre>'
    text = re.sub(r'```(?:\w*\n)?(.*?)```', replace_code_block, text, flags=re.DOTALL)

    # Inline code (`code`)
    text = re.sub(r'`([^`]+)`', rf'<code style="background-color: {code_bg}; color: {code_fg}; padding: 2px 4px; border-radius: 3px;">\1</code>', text)

    # Headers
    text = re.sub(r'^### (.+)$', r'<h4>\1</h4>', text, flags=re.MULTILINE)
    text = re.sub(r'^## (.+)$', r'<h3>\1</h3>', text, flags=re.MULTILINE)
    text = re.sub(r'^# (.+)$', r'<h2>\1</h2>', text, flags=re.MULTILINE)

    # Bold (**text** or __text__)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)

    # Italic (*text* or _text_) - careful not to match inside words
    text = re.sub(r'(?<!\w)\*([^*]+)\*(?!\w)', r'<i>\1</i>', text)
    text = re.sub(r'(?<!\w)_([^_]+)_(?!\w)', r'<i>\1</i>', text)

    # Links [text](url)
    text = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', text)

    # Bullet lists (- item or * item)
    text = re.sub(r'^[\-\*] (.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)
    # Wrap consecutive <li> in <ul>
    text = re.sub(r'((?:<li>.*?</li>\n?)+)', r'<ul>\1</ul>', text)

    # Numbered lists (1. item)
    text = re.sub(r'^\d+\. (.+)$', r'<li>\1</li>', text, flags=re.MULTILINE)

    # Line breaks - convert newlines to <br> (but not inside pre/code blocks)
    # Simple approach: just convert remaining newlines
    text = text.replace('\n', '<br>')

    # Clean up multiple <br> tags
    text = re.sub(r'(<br>){3,}', '<br><br>', text)

    return text


class MessageType:
    """Message type constants for visual differentiation."""
    TEXT = "text"           # Normal assistant text
    TOOL_USE = "tool_use"   # Tool invocation (muted, italic)
    SCRIPT = "script"       # Script code (monospace, dark bg)
    OUTPUT = "output"       # Script output (monospace, gray bg)
    ERROR = "error"         # Error message (red accent)
    USER = "user"           # User message


class ProgressTimeline(QFrame):
    """Compact progress timeline showing agent stages."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._script_count = 0
        self._current_stage = ""
        self._is_complete = False
        self._setup_ui()

    def _setup_ui(self):
        colors = get_ida_colors()
        self.setStyleSheet(f"background-color: {colors['window']};")

        self.layout = QHBoxLayout(self)
        self.layout.setContentsMargins(10, 4, 10, 4)
        self.layout.setSpacing(4)

        self.timeline_label = QLabel("")
        self.timeline_label.setStyleSheet(f"color: {colors['mid']}; font-size: 10px;")
        self.layout.addWidget(self.timeline_label)
        self.layout.addStretch()

        self.setVisible(False)

    def reset(self):
        """Reset the timeline for a new conversation."""
        self._script_count = 0
        self._current_stage = "User"
        self._is_complete = False
        self._update_display()
        self.setVisible(True)

    def add_stage(self, name: str):
        """Add a new stage to the timeline."""
        # Track scripts by parsing the number from "Script N"
        if name.startswith("Script "):
            try:
                self._script_count = int(name.split()[1])
            except (IndexError, ValueError):
                pass
        self._current_stage = name
        self._update_display()

    def complete(self):
        """Mark the timeline as complete."""
        self._is_complete = True
        self._current_stage = "Done"
        self._update_display()

    def hide_timeline(self):
        """Hide the timeline."""
        self.setVisible(False)

    def _update_display(self):
        """Update the timeline display with compact summary."""
        parts = []

        # Always show User as complete
        parts.append("<span style='color: #22c55e;'>✓ User</span>")

        # Show script count if any
        if self._script_count > 0:
            if self._is_complete:
                parts.append(f"<span style='color: #22c55e;'>✓ {self._script_count} scripts</span>")
            else:
                parts.append(f"<b style='color: #f59e0b;'>{self._script_count} scripts</b>")

        # Show current stage (Thinking, Retrying, Done)
        if self._is_complete:
            parts.append("<span style='color: #22c55e;'>✓ Done</span>")
        elif self._current_stage and self._current_stage not in ("User",) and not self._current_stage.startswith("Script"):
            parts.append(f"<b style='color: #f59e0b;'>{self._current_stage}</b>")

        self.timeline_label.setText(" → ".join(parts))


class ChatMessage(QFrame):
    """A single chat message bubble with optional status indicator."""

    def __init__(self, text: str, is_user: bool = True, is_processing: bool = False,
                 msg_type: str = MessageType.TEXT, parent=None):
        super().__init__(parent)
        self.is_user = is_user
        self._is_processing = is_processing
        self._msg_type = msg_type if not is_user else MessageType.USER
        self._blink_visible = True
        self._blink_timer = None
        self._status_indicator = None
        self._setup_ui(text)

    def _setup_ui(self, text: str):
        """Set up the message bubble UI."""
        colors = get_ida_colors()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        if self.is_user:
            # User message - right aligned, accent color background, plain QLabel
            self.message_widget = QLabel(text)
            self.message_widget.setWordWrap(True)
            self.message_widget.setTextInteractionFlags(
                Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
            )
            self.message_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
            layout.addStretch()
            self.message_widget.setStyleSheet(f"""
                QLabel {{
                    background-color: {colors['highlight']};
                    color: {colors['highlight_text']};
                    border-radius: 10px;
                    padding: 8px 12px;
                }}
            """)
            layout.addWidget(self.message_widget)
        else:
            # Status indicator for assistant messages (small dot)
            self._status_indicator = QLabel("●")
            self._status_indicator.setFixedWidth(16)
            self._status_indicator.setAlignment(Qt.AlignCenter | Qt.AlignTop)
            self._update_indicator_style()
            layout.addWidget(self._status_indicator)

            # Assistant message - QLabel with rich text for markdown
            self.message_widget = QLabel()
            self.message_widget.setTextFormat(Qt.RichText)
            self.message_widget.setWordWrap(True)
            self.message_widget.setTextInteractionFlags(
                Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard | Qt.LinksAccessibleByMouse
            )
            self.message_widget.setOpenExternalLinks(True)
            self.message_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)

            # Apply type-specific styling
            if self._msg_type == MessageType.TOOL_USE:
                # Tool use - muted, italic
                self.message_widget.setText(f"<i>{text}</i>")
                self.message_widget.setStyleSheet(f"""
                    QLabel {{
                        background-color: transparent;
                        color: {colors['mid']};
                        padding: 4px 8px;
                        font-size: 11px;
                    }}
                """)
            elif self._msg_type == MessageType.SCRIPT:
                # Script code - monospace, dark background
                self.message_widget.setText(f"<pre style='margin: 0;'>{text}</pre>")
                self.message_widget.setStyleSheet(f"""
                    QLabel {{
                        background-color: #1e1e1e;
                        color: #d4d4d4;
                        border-radius: 6px;
                        padding: 8px 12px;
                        font-family: monospace;
                        font-size: 11px;
                    }}
                """)
            elif self._msg_type == MessageType.OUTPUT:
                # Script output - monospace, gray background
                self.message_widget.setText(f"<pre style='margin: 0;'>{text}</pre>")
                self.message_widget.setStyleSheet(f"""
                    QLabel {{
                        background-color: #2d2d2d;
                        color: #a0a0a0;
                        border-radius: 6px;
                        padding: 8px 12px;
                        font-family: monospace;
                        font-size: 11px;
                    }}
                """)
            elif self._msg_type == MessageType.ERROR:
                # Error - red accent
                self.message_widget.setText(markdown_to_html(text))
                self.message_widget.setStyleSheet(f"""
                    QLabel {{
                        background-color: #2d1f1f;
                        color: #f87171;
                        border: 1px solid #dc2626;
                        border-radius: 10px;
                        padding: 8px 12px;
                    }}
                """)
            else:
                # Default text styling
                self.message_widget.setText(markdown_to_html(text))
                self.message_widget.setStyleSheet(f"""
                    QLabel {{
                        background-color: {colors['alt_base']};
                        color: {colors['text']};
                        border-radius: 10px;
                        padding: 8px 12px;
                    }}
                """)

            layout.addWidget(self.message_widget)
            layout.addStretch()

            # Start blinking if processing
            if self._is_processing:
                self._start_blinking()

    def _update_indicator_style(self):
        """Update the status indicator color."""
        if not self._status_indicator:
            return
        if self._is_processing:
            # Yellow/orange for processing, blink visibility
            color = "#f59e0b" if self._blink_visible else "transparent"
        else:
            # Green for complete
            color = "#22c55e"
        self._status_indicator.setStyleSheet(f"QLabel {{ color: {color}; font-size: 10px; }}")

    def _start_blinking(self):
        """Start the blinking animation."""
        if self._blink_timer:
            return
        self._blink_timer = QTimer(self)
        self._blink_timer.timeout.connect(self._toggle_blink)
        self._blink_timer.start(500)  # Blink every 500ms

    def _stop_blinking(self):
        """Stop the blinking animation."""
        if self._blink_timer:
            self._blink_timer.stop()
            self._blink_timer = None
        self._blink_visible = True

    def _toggle_blink(self):
        """Toggle blink visibility."""
        self._blink_visible = not self._blink_visible
        self._update_indicator_style()

    def set_complete(self):
        """Mark this message as complete (green indicator)."""
        self._is_processing = False
        self._stop_blinking()
        self._update_indicator_style()

    def update_text(self, text: str):
        """Update the message text."""
        if self.is_user:
            self.message_widget.setText(text)
        else:
            self.message_widget.setText(markdown_to_html(text))

    def update_max_width(self, container_width: int):
        """Update max width to 80% of container width."""
        max_width = int(container_width * 0.80)
        self.message_widget.setMaximumWidth(max_width)


class ChatHistoryWidget(QScrollArea):
    """Scrollable chat history container."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._current_processing_message: ChatMessage | None = None
        self._setup_ui()

    def _setup_ui(self):
        """Set up the chat history UI."""
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.NoFrame)

        # Container widget for messages
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setSpacing(8)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.addStretch(1)  # Stretch at top pushes messages to bottom

        self.setWidget(self.container)

    def add_message(self, text: str, is_user: bool = True, is_processing: bool = False,
                    msg_type: str = MessageType.TEXT) -> ChatMessage:
        """Add a message to the chat history."""
        message = ChatMessage(text, is_user, is_processing, msg_type)
        # Set initial max width to 80% of container
        message.update_max_width(self.viewport().width())
        self.layout.addWidget(message)

        # Track processing message
        if is_processing:
            self._current_processing_message = message

        self.scroll_to_bottom()
        return message

    def mark_current_complete(self):
        """Mark the current processing message as complete."""
        if self._current_processing_message:
            self._current_processing_message.set_complete()
            self._current_processing_message = None

    def scroll_to_bottom(self):
        """Scroll the chat history to the bottom."""
        QTimer.singleShot(10, lambda: self.verticalScrollBar().setValue(
            self.verticalScrollBar().maximum()
        ))

    def add_collapsible(self, title: str, content: str, collapsed: bool = True) -> CollapsibleSection:
        """Add a collapsible section to the chat history."""
        section = CollapsibleSection(title, content, collapsed)
        self.layout.addWidget(section)
        self.scroll_to_bottom()
        return section

    def clear_history(self):
        """Clear all messages from the chat history."""
        self._current_processing_message = None
        # Remove all widgets except the stretch at index 0
        while self.layout.count() > 1:
            item = self.layout.takeAt(1)  # Always take from index 1, leaving stretch at 0
            if item.widget():
                item.widget().deleteLater()

    def resizeEvent(self, event):
        """Update message max widths when container is resized."""
        super().resizeEvent(event)
        new_width = self.viewport().width()
        # Update all ChatMessage widgets
        for i in range(self.layout.count()):
            item = self.layout.itemAt(i)
            if item and item.widget() and isinstance(item.widget(), ChatMessage):
                item.widget().update_max_width(new_width)


class ChatInputWidget(QPlainTextEdit):
    """Multi-line text input with Enter to send and history navigation."""

    message_submitted = Signal(str)
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[str] = []
        self._history_index = -1  # -1 means not browsing history
        self._current_input = ""  # Stores current input when browsing history
        self._setup_ui()

    def set_history(self, messages: list[str]):
        """Set the message history for up/down navigation.

        Args:
            messages: List of previous user messages (oldest first).
        """
        self._history = messages
        self._history_index = -1

    def add_to_history(self, message: str):
        """Add a message to the history.

        Args:
            message: The message to add.
        """
        # Don't add duplicates of the last message
        if not self._history or self._history[-1] != message:
            self._history.append(message)
        self._history_index = -1

    def _setup_ui(self):
        """Set up the input widget UI."""
        colors = get_ida_colors()

        self.setPlaceholderText("Type a message... (↑↓ history, Enter send, Esc cancel)")
        self.setMaximumHeight(100)
        self.setMinimumHeight(40)
        self.setStyleSheet(f"""
            QPlainTextEdit {{
                background-color: {colors['base']};
                color: {colors['text']};
                border: 1px solid {colors['mid']};
                border-radius: 6px;
                padding: 6px 10px;
            }}
            QPlainTextEdit:focus {{
                border: 1px solid {colors['highlight']};
            }}
        """)

    def keyPressEvent(self, event: QKeyEvent):
        """Handle special keys: Enter, Escape, Up/Down for history."""
        if event.key() == Qt.Key_Escape:
            # Escape: cancel current operation
            self.cancel_requested.emit()
        elif event.key() == Qt.Key_Up:
            # Up arrow: navigate to older history
            self._navigate_history(-1)
        elif event.key() == Qt.Key_Down:
            # Down arrow: navigate to newer history
            self._navigate_history(1)
        elif event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            if event.modifiers() & Qt.ShiftModifier:
                # Shift+Enter: insert new line
                super().keyPressEvent(event)
            else:
                # Enter: submit message
                text = self.toPlainText().strip()
                if text:
                    self.add_to_history(text)
                    self.message_submitted.emit(text)
                    self.clear()
                    self._history_index = -1
                    self.setFocus()  # Keep focus on input
        else:
            super().keyPressEvent(event)

    def _navigate_history(self, direction: int):
        """Navigate through message history.

        Args:
            direction: -1 for older (up), +1 for newer (down)
        """
        if not self._history:
            return

        # Save current input when starting to browse
        if self._history_index == -1:
            self._current_input = self.toPlainText()

        # Calculate new index
        if direction < 0:  # Up - go to older
            if self._history_index == -1:
                # Start browsing from the end (most recent)
                new_index = len(self._history) - 1
            else:
                new_index = max(0, self._history_index - 1)
        else:  # Down - go to newer
            if self._history_index == -1:
                # Already at current input, do nothing
                return
            new_index = self._history_index + 1
            if new_index >= len(self._history):
                # Return to current input
                self._history_index = -1
                self.setPlainText(self._current_input)
                # Move cursor to end
                cursor = self.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self.setTextCursor(cursor)
                return

        # Set the history item
        self._history_index = new_index
        self.setPlainText(self._history[self._history_index])
        # Move cursor to end
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.setTextCursor(cursor)


class PluginCallback(ChatCallback):
    """Qt widget output implementation of ChatCallback.

    Uses Qt signals to safely update UI from any thread.
    """

    def __init__(self, signals: "AgentSignals"):
        self.signals = signals

    def on_turn_start(self, turn: int, max_turns: int) -> None:
        self.signals.turn_start.emit(turn, max_turns)

    def on_thinking(self) -> None:
        self.signals.thinking.emit()

    def on_thinking_done(self) -> None:
        self.signals.thinking_done.emit()

    def on_tool_use(self, tool_name: str, details: str) -> None:
        self.signals.tool_use.emit(tool_name, details)

    def on_text(self, text: str) -> None:
        self.signals.text.emit(text)

    def on_script_code(self, code: str) -> None:
        self.signals.script_code.emit(code)

    def on_script_output(self, output: str) -> None:
        self.signals.script_output.emit(output)

    def on_error(self, error: str) -> None:
        self.signals.error.emit(error)

    def on_result(self, num_turns: int, cost: float | None) -> None:
        self.signals.result.emit(num_turns, cost or 0.0)


class AgentSignals(QObject):
    """Qt signals for agent callbacks."""

    turn_start = Signal(int, int)
    thinking = Signal()
    thinking_done = Signal()
    tool_use = Signal(str, str)
    text = Signal(str)
    script_code = Signal(str)
    script_output = Signal(str)
    error = Signal(str)
    result = Signal(int, float)
    finished = Signal()
    connection_ready = Signal()
    connection_error = Signal(str)


class AgentWorker(QThread):
    """Background worker for running async agent calls."""

    def __init__(self, db: Database, script_executor: Callable[[str], str],
                 history: MessageHistory, parent=None):
        super().__init__(parent)
        self.db = db
        self.script_executor = script_executor
        self.history = history
        self.signals = AgentSignals()
        self.callback = PluginCallback(self.signals)
        self.core: IDAChatCore | None = None
        self._pending_message: str | None = None
        self._should_connect = False
        self._should_disconnect = False
        self._should_cancel = False
        self._should_new_session = False
        self._running = True

    def request_connect(self):
        """Request connection to agent."""
        self._should_connect = True
        if not self.isRunning():
            self.start()

    def request_disconnect(self):
        """Request disconnection from agent."""
        self._should_disconnect = True
        self._running = False

    def request_cancel(self):
        """Request cancellation of current operation."""
        self._should_cancel = True
        if self.core:
            self.core.request_cancel()

    def request_new_session(self):
        """Request starting a new session for history tracking."""
        self._should_new_session = True

    def send_message(self, message: str):
        """Queue a message to be sent to the agent."""
        self._pending_message = message
        if not self.isRunning():
            self.start()

    def run(self):
        """Run the async event loop in this thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            loop.run_until_complete(self._async_run())
        finally:
            loop.close()

    async def _async_run(self):
        """Main async loop."""
        # Handle connection request
        if self._should_connect:
            self._should_connect = False
            try:
                # Start initial session for history
                self.history.start_new_session()

                self.core = IDAChatCore(
                    self.db,
                    self.callback,
                    script_executor=self.script_executor,
                    history=self.history,
                )
                await self.core.connect()
                self.signals.connection_ready.emit()
            except Exception as e:
                self.signals.connection_error.emit(str(e))
                return

        # Process messages while running
        while self._running:
            # Handle new session request (e.g., after Clear)
            if self._should_new_session:
                self._should_new_session = False
                self.history.start_new_session()

            if self._pending_message:
                message = self._pending_message
                self._pending_message = None
                try:
                    await self.core.process_message(message)
                except Exception as e:
                    self.signals.error.emit(str(e))
                self.signals.finished.emit()

            # Check for disconnect request
            if self._should_disconnect:
                break

            # Small sleep to avoid busy loop
            await asyncio.sleep(0.1)

        # Handle disconnection
        if self.core:
            await self.core.disconnect()


class TestConnectionWorker(QThread):
    """Background thread for testing Claude connection."""

    finished = Signal(bool, str)  # (success, message)

    def __init__(self, parent=None):
        super().__init__(parent)

    def run(self):
        """Run the connection test."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            success, message = loop.run_until_complete(test_claude_connection())
            self.finished.emit(success, message)
        except Exception as e:
            self.finished.emit(False, str(e))
        finally:
            loop.close()


class OnboardingPanel(QFrame):
    """Onboarding panel for first-time setup and settings configuration."""

    onboarding_complete = Signal()  # Emitted when user clicks Save & Start

    def __init__(self, parent=None):
        super().__init__(parent)
        self._test_worker: TestConnectionWorker | None = None
        self._setup_ui()

    def _setup_ui(self):
        colors = get_ida_colors()

        self.setStyleSheet(f"""
            QFrame {{
                background-color: {colors['base']};
                border-radius: 8px;
            }}
        """)

        # Main horizontal layout for two columns
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # Left column (30%) - Image
        image_container = QWidget()
        image_layout = QVBoxLayout(image_container)
        image_layout.setContentsMargins(0, 0, 0, 0)

        image_label = QLabel()
        splash_path = Path(__file__).parent / "splash.png"
        if splash_path.exists():
            pixmap = QPixmap(str(splash_path))
            image_label.setPixmap(pixmap.scaled(
                300, 400,
                Qt.KeepAspectRatio,
                Qt.SmoothTransformation
            ))
        image_label.setAlignment(Qt.AlignCenter)
        image_layout.addWidget(image_label)
        image_layout.addStretch()

        main_layout.addWidget(image_container, stretch=30)

        # Right column (70%) - Settings
        settings_container = QWidget()
        layout = QVBoxLayout(settings_container)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Title
        title = QLabel("Welcome to IDA Chat")
        title.setStyleSheet(f"""
            QLabel {{
                color: {colors['text']};
                font-size: 18px;
                font-weight: bold;
            }}
        """)
        layout.addWidget(title)

        # Instructions
        instructions = QLabel("Configure your Claude authentication:")
        instructions.setStyleSheet(f"QLabel {{ color: {colors['mid']}; }}")
        layout.addWidget(instructions)

        # Radio buttons for auth type
        self.auth_group = QButtonGroup(self)

        # Option 1: System (use existing Claude installation)
        self.radio_system = QRadioButton("Use Claude settings on my machine")
        self.radio_system.setStyleSheet(f"QRadioButton {{ color: {colors['text']}; }}")
        self.radio_system.setChecked(True)
        self.auth_group.addButton(self.radio_system, 0)
        layout.addWidget(self.radio_system)

        system_hint = QLabel("    Recommended if Claude Code is installed")
        system_hint.setStyleSheet(f"QLabel {{ color: {colors['mid']}; font-size: 11px; }}")
        layout.addWidget(system_hint)

        # Option 2: OAuth (Claude subscription)
        self.radio_oauth = QRadioButton("Claude account (Pro, Max, Team, or Enterprise)")
        self.radio_oauth.setStyleSheet(f"QRadioButton {{ color: {colors['text']}; }}")
        self.auth_group.addButton(self.radio_oauth, 1)
        layout.addWidget(self.radio_oauth)

        # Option 3: API Key (Anthropic Console)
        self.radio_api_key = QRadioButton("Anthropic Console account (API billing)")
        self.radio_api_key.setStyleSheet(f"QRadioButton {{ color: {colors['text']}; }}")
        self.auth_group.addButton(self.radio_api_key, 2)
        layout.addWidget(self.radio_api_key)

        # Key input field (hidden for system option)
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Paste your key here...")
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.setStyleSheet(f"""
            QLineEdit {{
                background-color: {colors['alt_base']};
                color: {colors['text']};
                border: 1px solid {colors['mid']};
                border-radius: 4px;
                padding: 8px;
            }}
            QLineEdit:focus {{
                border-color: {colors['highlight']};
            }}
        """)
        self.key_input.hide()  # Hidden by default (system option selected)
        layout.addWidget(self.key_input)

        # Connect radio buttons to show/hide key input
        self.auth_group.buttonClicked.connect(self._on_auth_type_changed)

        # Buttons row
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(12)

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {colors['button']};
                color: {colors['button_text']};
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
            }}
            QPushButton:hover {{
                background-color: {colors['highlight']};
                color: {colors['highlight_text']};
            }}
            QPushButton:disabled {{
                background-color: {colors['mid']};
            }}
        """)
        self.test_btn.clicked.connect(self._on_test_clicked)
        buttons_layout.addWidget(self.test_btn)

        self.save_btn = QPushButton("Save && Start")
        self.save_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {colors['highlight']};
                color: {colors['highlight_text']};
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {colors['button']};
                color: {colors['button_text']};
            }}
            QPushButton:disabled {{
                background-color: {colors['mid']};
            }}
        """)
        self.save_btn.clicked.connect(self._on_save_clicked)
        buttons_layout.addWidget(self.save_btn)

        layout.addLayout(buttons_layout)

        # Status label
        self.status_label = QLabel("Not configured")
        self.status_label.setStyleSheet(f"QLabel {{ color: {colors['mid']}; }}")
        layout.addWidget(self.status_label)

        # Response area (for showing joke on successful test)
        self.response_label = QLabel()
        self.response_label.setWordWrap(True)
        self.response_label.setStyleSheet(f"""
            QLabel {{
                color: {colors['text']};
                background-color: {colors['alt_base']};
                border-radius: 4px;
                padding: 12px;
            }}
        """)
        self.response_label.hide()
        layout.addWidget(self.response_label)

        layout.addStretch()

        main_layout.addWidget(settings_container, stretch=70)

    def _on_auth_type_changed(self, button):
        """Show/hide key input based on selected auth type."""
        if button == self.radio_system:
            self.key_input.hide()
        else:
            self.key_input.show()

    def _on_test_clicked(self):
        """Run connection test."""
        self.test_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.status_label.setText("Testing connection...")
        self.response_label.hide()

        # Apply settings to environment before testing
        self._apply_current_settings()

        # Start test worker
        self._test_worker = TestConnectionWorker(self)
        self._test_worker.finished.connect(self._on_test_finished)
        self._test_worker.start()

    def _on_test_finished(self, success: bool, message: str):
        """Handle test result."""
        colors = get_ida_colors()
        self.test_btn.setEnabled(True)
        self.save_btn.setEnabled(True)

        if success:
            self.status_label.setText("✓ Connected! You're all set.")
            self.status_label.setStyleSheet(f"QLabel {{ color: #4CAF50; }}")  # Green
            self.response_label.setText(message)
            self.response_label.show()
        else:
            self.status_label.setText(f"✗ Connection failed: {message}")
            self.status_label.setStyleSheet(f"QLabel {{ color: #F44336; }}")  # Red
            self.response_label.hide()

    def _apply_current_settings(self):
        """Apply current UI settings to environment variables."""
        auth_type = self._get_auth_type()
        api_key = self.key_input.text().strip() if auth_type != "system" else None

        if auth_type == "system":
            pass  # Use existing system configuration
        elif auth_type == "oauth" and api_key:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = api_key
        elif auth_type == "api_key" and api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key

    def _get_auth_type(self) -> str:
        """Get the selected auth type."""
        if self.radio_system.isChecked():
            return "system"
        elif self.radio_oauth.isChecked():
            return "oauth"
        else:
            return "api_key"

    def _on_save_clicked(self):
        """Save settings and emit completion signal."""
        auth_type = self._get_auth_type()
        api_key = self.key_input.text().strip() if auth_type != "system" else None

        # Validate key input for non-system auth types
        if auth_type != "system" and not api_key:
            colors = get_ida_colors()
            self.status_label.setText("Please enter your API key")
            self.status_label.setStyleSheet(f"QLabel {{ color: #F44336; }}")
            return

        # Save settings
        save_auth_settings(auth_type, api_key)

        # Apply to environment
        self._apply_current_settings()

        # Emit completion signal
        self.onboarding_complete.emit()

    def load_current_settings(self):
        """Load current settings into the UI (for settings mode)."""
        auth_type = get_auth_type()
        api_key = get_api_key()

        if auth_type == "system":
            self.radio_system.setChecked(True)
            self.key_input.hide()
        elif auth_type == "oauth":
            self.radio_oauth.setChecked(True)
            self.key_input.show()
            if api_key:
                self.key_input.setText(api_key)
        elif auth_type == "api_key":
            self.radio_api_key.setChecked(True)
            self.key_input.show()
            if api_key:
                self.key_input.setText(api_key)

        # Reset status
        colors = get_ida_colors()
        self.status_label.setText("Settings loaded")
        self.status_label.setStyleSheet(f"QLabel {{ color: {colors['mid']}; }}")
        self.response_label.hide()


class IDAChatForm(ida_kernwin.PluginForm):
    """Main chat widget form."""

    def OnCreate(self, form):
        """Called when the widget is created."""
        self.parent = self.FormToPyQtWidget(form)
        self.worker: AgentWorker | None = None
        self._is_processing = False
        self._current_message = None  # Track current blinking message
        self._current_turn = 0
        self._max_turns = 20
        self._total_cost = 0.0
        self._script_count = 0
        self._last_had_error = False
        self._summary_mode = False  # False = detailed, True = summary

        # Set larger minimum width for the panel
        self.parent.setMinimumWidth(800)

        self._create_ui()

        # Apply saved auth settings to environment
        apply_auth_to_environment()

        # Check if wizard should be shown
        if get_show_wizard():
            self._show_onboarding()
        else:
            self._init_agent()

    def _create_script_executor(self, db: Database) -> Callable[[str], str]:
        """Create a script executor that runs on the main thread.

        IDA operations must be performed on the main thread. This executor
        uses ida_kernwin.execute_sync() to ensure scripts run safely.
        """
        def execute_on_main_thread(code: str) -> str:
            result = [""]

            def run_script():
                old_stdout = sys.stdout
                sys.stdout = captured = StringIO()
                try:
                    exec(code, {"db": db, "print": print})
                    result[0] = captured.getvalue()
                except Exception as e:
                    result[0] = f"Script error: {e}"
                finally:
                    sys.stdout = old_stdout
                return 1  # Required return for execute_sync

            ida_kernwin.execute_sync(run_script, ida_kernwin.MFF_FAST)
            return result[0]

        return execute_on_main_thread

    def _init_agent(self):
        """Initialize the agent worker."""
        try:
            db = Database.open()
            script_executor = self._create_script_executor(db)

            # Create message history for this binary
            self.history = MessageHistory(db.path)

            self.worker = AgentWorker(db, script_executor, self.history)

            # Connect signals
            self.worker.signals.connection_ready.connect(self._on_connection_ready)
            self.worker.signals.connection_error.connect(self._on_connection_error)
            self.worker.signals.turn_start.connect(self._on_turn_start)
            self.worker.signals.thinking.connect(self._on_thinking)
            self.worker.signals.thinking_done.connect(self._on_thinking_done)
            self.worker.signals.tool_use.connect(self._on_tool_use)
            self.worker.signals.text.connect(self._on_text)
            self.worker.signals.script_code.connect(self._on_script_code)
            self.worker.signals.script_output.connect(self._on_script_output)
            self.worker.signals.error.connect(self._on_error)
            self.worker.signals.result.connect(self._on_result)
            self.worker.signals.finished.connect(self._on_finished)

            # Start connection
            self.worker.request_connect()
        except Exception as e:
            self.chat_history.add_message(f"Error initializing agent: {e}", is_user=False)

    def _show_onboarding(self):
        """Show onboarding panel, hide chat UI."""
        self.onboarding_panel.show()
        self.chat_history.hide()
        self.input_container.hide()
        self.progress_timeline.hide()

    def _show_settings(self):
        """Show settings panel (re-use onboarding panel)."""
        # Load current settings into the panel
        self.onboarding_panel.load_current_settings()
        self._show_onboarding()

    def _on_onboarding_complete(self):
        """Handle successful onboarding."""
        self.onboarding_panel.hide()
        self.chat_history.show()
        self.input_container.show()
        self._init_agent()

    def _on_connection_ready(self):
        """Called when agent connection is established."""
        self.chat_history.add_message("Agent connected and ready!", is_user=False)
        self.input_widget.setEnabled(True)
        self.input_widget.setFocus()

        # Load message history for up/down arrow navigation
        if hasattr(self, 'history'):
            user_messages = self.history.get_all_user_messages()
            self.input_widget.set_history(user_messages)

    def _on_connection_error(self, error: str):
        """Called when agent connection fails."""
        self.chat_history.add_message(f"Connection error: {error}", is_user=False)

    def _on_turn_start(self, turn: int, max_turns: int):
        """Called at the start of each agentic turn."""
        self._current_turn = turn
        self._max_turns = max_turns
        self.status_label.setText(f"Turn {turn}/{max_turns}")

    def _on_thinking(self):
        """Called when agent starts processing."""
        self._is_processing = True
        # Mark previous message as complete before starting new turn
        if self._current_message:
            self._current_message.set_complete()
        self.input_widget.setEnabled(False)

        # Check if this is a retry after error
        if self._last_had_error:
            self._last_had_error = False
            # Update timeline
            self.progress_timeline.add_stage("Retrying")
            # Add retry message
            self._current_message = self.chat_history.add_message(
                "🔄 Retrying after error...", is_user=False, is_processing=True
            )
        else:
            # Update timeline
            self.progress_timeline.add_stage("Thinking")
            # Add thinking message with blinking indicator
            self._current_message = self.chat_history.add_message(
                "[Thinking...]", is_user=False, is_processing=True
            )

    def _on_thinking_done(self):
        """Called when agent produces first output."""
        # Remove the thinking message (last widget in layout, stretch is at index 0)
        if self.chat_history.layout.count() > 1:
            item = self.chat_history.layout.takeAt(self.chat_history.layout.count() - 1)
            if item and item.widget():
                item.widget().deleteLater()
        self._current_message = None

    def _add_processing_message(self, text: str, msg_type: str = MessageType.TEXT) -> None:
        """Add a new processing message, marking previous one as complete."""
        # Mark previous message as complete (green)
        if self._current_message:
            self._current_message.set_complete()
        # Add new blinking message
        self._current_message = self.chat_history.add_message(
            text, is_user=False, is_processing=True, msg_type=msg_type
        )

    def _on_tool_use(self, tool_name: str, details: str):
        """Called when agent uses a tool."""
        # Skip tool use messages in summary mode
        if self._summary_mode:
            return
        tool_msg = f"[{tool_name}]"
        if details:
            tool_msg += f" {details}"
        self._add_processing_message(tool_msg, MessageType.TOOL_USE)

    def _on_text(self, text: str):
        """Called when agent outputs text."""
        if text.strip():
            self._add_processing_message(text)

    def _on_script_code(self, code: str):
        """Called with script code before execution."""
        import html
        # Update timeline
        self._script_count += 1
        self.progress_timeline.add_stage(f"Script {self._script_count}")
        # Update status to show running script
        self.status_label.setText(f"Turn {self._current_turn}/{self._max_turns} • Running script {self._script_count}...")
        # Skip script code in summary mode
        if self._summary_mode:
            return
        # Show preview of the script
        lines = code.strip().split('\n')
        preview = '\n'.join(lines[:5])
        if len(lines) > 5:
            preview += f"\n... ({len(lines) - 5} more lines)"
        self._add_processing_message(html.escape(preview), MessageType.SCRIPT)

    def _on_script_output(self, output: str):
        """Called with script output."""
        if output.strip():
            import html
            # Check if this is an error output
            is_error = output.strip().startswith("Script error:")
            if is_error:
                self._last_had_error = True
                # Always show errors, even in summary mode
                self._add_processing_message(output, MessageType.ERROR)
            elif self._summary_mode:
                # Skip normal output in summary mode
                return
            # Use collapsible section for long outputs
            elif CollapsibleSection.should_collapse(output):
                # Mark previous message as complete
                if self._current_message:
                    self._current_message.set_complete()
                self.chat_history.add_collapsible("Script Output", output, collapsed=True)
                self._current_message = None
            else:
                self._add_processing_message(html.escape(output), MessageType.OUTPUT)

    def _on_error(self, error: str):
        """Called when an error occurs."""
        self._add_processing_message(f"Error: {error}", MessageType.ERROR)

    def _on_result(self, num_turns: int, cost: float):
        """Called when agent returns result with stats."""
        self._total_cost += cost
        self.cost_label.setText(f"${self._total_cost:.4f}")

    def _on_finished(self):
        """Called when agent finishes processing."""
        self._is_processing = False
        self.input_widget.setEnabled(True)
        self.input_widget.setFocus()
        self.status_label.setText("Ready")
        self.progress_timeline.complete()
        # Mark the last message as complete (green)
        if self._current_message:
            self._current_message.set_complete()
            self._current_message = None

    def _create_ui(self):
        """Create the chat interface UI."""
        colors = get_ida_colors()

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 6, 10, 6)

        title = QLabel(PLUGIN_NAME)
        title.setStyleSheet(f"""
            QLabel {{
                color: {colors['window_text']};
                font-weight: bold;
            }}
        """)
        header_layout.addWidget(title)
        header_layout.addStretch()

        # Settings button (gear icon)
        settings_btn = QPushButton("⚙")
        settings_btn.setFixedSize(28, 28)
        settings_btn.setToolTip("Settings")
        settings_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {colors['mid']};
                border: none;
                font-size: 14px;
            }}
            QPushButton:hover {{
                color: {colors['window_text']};
            }}
        """)
        settings_btn.clicked.connect(self._show_settings)
        header_layout.addWidget(settings_btn)

        # Clear button
        clear_btn = QPushButton("Clear")
        clear_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {colors['mid']};
                border: none;
                padding: 4px 8px;
            }}
            QPushButton:hover {{
                color: {colors['window_text']};
            }}
        """)
        clear_btn.clicked.connect(self._on_clear)
        header_layout.addWidget(clear_btn)

        # View mode toggle button
        self.view_mode_btn = QPushButton("Detailed")
        self.view_mode_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: transparent;
                color: {colors['mid']};
                border: 1px solid {colors['mid']};
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
            }}
            QPushButton:hover {{
                color: {colors['window_text']};
                border-color: {colors['window_text']};
            }}
        """)
        self.view_mode_btn.clicked.connect(self._on_toggle_view_mode)
        header_layout.addWidget(self.view_mode_btn)

        layout.addWidget(header)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet(f"background-color: {colors['mid']};")
        separator.setFixedHeight(1)
        layout.addWidget(separator)

        # Onboarding panel (shown on first launch or when settings clicked)
        self.onboarding_panel = OnboardingPanel()
        self.onboarding_panel.onboarding_complete.connect(self._on_onboarding_complete)
        self.onboarding_panel.hide()  # Hidden by default, shown if not onboarded
        layout.addWidget(self.onboarding_panel)

        # Progress timeline (hidden by default)
        self.progress_timeline = ProgressTimeline()
        layout.addWidget(self.progress_timeline)

        # Chat history area (takes most space)
        self.chat_history = ChatHistoryWidget()
        layout.addWidget(self.chat_history, stretch=1)

        # Input area at bottom
        self.input_container = QWidget()
        input_layout = QHBoxLayout(self.input_container)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(8)

        # Text input (Enter to send, Escape to cancel)
        self.input_widget = ChatInputWidget()
        self.input_widget.message_submitted.connect(self._on_message_submitted)
        self.input_widget.cancel_requested.connect(self._on_cancel)
        input_layout.addWidget(self.input_widget, stretch=1)

        layout.addWidget(self.input_container)

        # Status bar at bottom
        self.status_bar = QWidget()
        status_layout = QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(10, 4, 10, 4)

        self.status_label = QLabel("Ready")
        self.status_label.setStyleSheet(f"color: {colors['mid']}; font-size: 11px;")
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()

        self.cost_label = QLabel("")
        self.cost_label.setStyleSheet(f"color: {colors['mid']}; font-size: 11px;")
        status_layout.addWidget(self.cost_label)

        layout.addWidget(self.status_bar)

        self.parent.setLayout(layout)

        # Add welcome message
        self._add_welcome_message()

    def _add_welcome_message(self):
        """Add a welcome message to the chat."""
        welcome_text = (
            "Welcome to IDA Chat! Connecting to Claude Agent SDK..."
        )
        self.chat_history.add_message(welcome_text, is_user=False)
        # Disable input until agent is connected
        self.input_widget.setEnabled(False)

    def _on_message_submitted(self, text: str):
        """Handle message submission from input widget."""
        self._send_message(text)

    def _send_message(self, text: str):
        """Send a message to the agent."""
        if not self.worker or self._is_processing:
            return

        # Reset timeline for new conversation
        self.progress_timeline.reset()
        self._script_count = 0
        self._last_had_error = False

        # Add user message to chat
        self.chat_history.add_message(text, is_user=True)

        # Send to agent
        self.worker.send_message(text)

    def _on_cancel(self):
        """Cancel the current agent operation."""
        if self.worker and self._is_processing:
            self.worker.request_cancel()
            self.status_label.setText("Cancelling...")

    def _on_toggle_view_mode(self):
        """Toggle between detailed and summary view modes."""
        self._summary_mode = not self._summary_mode
        if self._summary_mode:
            self.view_mode_btn.setText("Summary")
        else:
            self.view_mode_btn.setText("Detailed")

    def _on_clear(self):
        """Clear the chat history."""
        self.chat_history.clear_history()
        self._total_cost = 0.0
        self._script_count = 0
        self.cost_label.setText("")
        self.progress_timeline.hide_timeline()

        # Start a new session for history tracking
        if self.worker:
            self.worker.request_new_session()

        # Add ready message (agent already connected)
        self.chat_history.add_message("Chat cleared. Ready for new conversation.", is_user=False)
        self.input_widget.setEnabled(True)
        self.input_widget.setFocus()

    def OnClose(self, form):
        """Called when the widget is closed."""
        if self.worker:
            self.worker.request_disconnect()
            self.worker.wait(5000)  # Wait up to 5 seconds for clean shutdown
            self.worker = None


class ToggleWidgetHandler(ida_kernwin.action_handler_t):
    """Handler to toggle the dockable widget."""

    def __init__(self, plugin):
        ida_kernwin.action_handler_t.__init__(self)
        self.plugin = plugin

    def activate(self, ctx):
        """Toggle widget visibility."""
        self.plugin.toggle_widget()
        return 1

    def update(self, ctx):
        return ida_kernwin.AST_ENABLE_ALWAYS


class IDAChatPlugin(ida_idaapi.plugin_t):
    """Main plugin class."""

    flags = ida_idaapi.PLUGIN_KEEP
    comment = PLUGIN_COMMENT
    help = PLUGIN_HELP
    wanted_name = PLUGIN_NAME
    wanted_hotkey = ""

    def init(self):
        """Initialize the plugin."""
        self.form = None

        # Register toggle action
        action_desc = ida_kernwin.action_desc_t(
            ACTION_ID,
            ACTION_NAME,
            ToggleWidgetHandler(self),
            ACTION_HOTKEY,
            ACTION_TOOLTIP,
            -1
        )

        if not ida_kernwin.register_action(action_desc):
            ida_kernwin.msg(f"{PLUGIN_NAME}: Failed to register action\n")
            return ida_idaapi.PLUGIN_SKIP

        ida_kernwin.attach_action_to_menu(
            "View/",
            ACTION_ID,
            ida_kernwin.SETMENU_APP
        )

        ida_kernwin.msg(f"{PLUGIN_NAME}: Loaded (use {ACTION_HOTKEY} to toggle)\n")
        return ida_idaapi.PLUGIN_KEEP

    def toggle_widget(self):
        """Show or hide the dockable widget."""
        widget = ida_kernwin.find_widget(WIDGET_TITLE)

        if widget:
            ida_kernwin.close_widget(widget, 0)
            self.form = None
        else:
            self.form = IDAChatForm()
            self.form.Show(
                WIDGET_TITLE,
                options=(
                    ida_kernwin.PluginForm.WOPN_PERSIST |
                    ida_kernwin.PluginForm.WOPN_DP_RIGHT |
                    ida_kernwin.PluginForm.WOPN_DP_SZHINT
                )
            )
            # Dock to the right side panel
            ida_kernwin.set_dock_pos(
                WIDGET_TITLE,
                'IDATopLevelDockArea',
                ida_kernwin.DP_RIGHT | ida_kernwin.DP_SZHINT
            )

    def run(self, arg):
        """Called when plugin is invoked directly."""
        self.toggle_widget()

    def term(self):
        """Clean up when plugin is unloaded."""
        widget = ida_kernwin.find_widget(WIDGET_TITLE)
        if widget:
            ida_kernwin.close_widget(widget, 0)

        ida_kernwin.detach_action_from_menu("View/", ACTION_ID)
        ida_kernwin.unregister_action(ACTION_ID)

        ida_kernwin.msg(f"{PLUGIN_NAME}: Unloaded\n")


def PLUGIN_ENTRY():
    """Plugin entry point."""
    return IDAChatPlugin()
