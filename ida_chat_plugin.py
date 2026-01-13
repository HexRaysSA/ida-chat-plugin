"""
IDA Chat - LLM Chat Client Plugin for IDA Pro

A dockable chat interface powered by Claude Agent SDK for
AI-assisted reverse engineering within IDA Pro.
"""

import asyncio
import re
import sys
from io import StringIO
from pathlib import Path
from typing import Callable

import ida_idaapi
import ida_kernwin
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
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, QTimer
from PySide6.QtGui import QKeyEvent, QPalette, QFont

# Ensure local modules are importable
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from ida_chat_core import IDAChatCore, ChatCallback


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

    # Escape HTML first
    text = html.escape(text)

    # Code blocks (``` ... ```) - must be before inline code
    def replace_code_block(match):
        code = match.group(1)
        return f'<pre style="background-color: #2d2d2d; color: #f8f8f2; padding: 8px; border-radius: 4px; overflow-x: auto;"><code>{code}</code></pre>'
    text = re.sub(r'```(?:\w*\n)?(.*?)```', replace_code_block, text, flags=re.DOTALL)

    # Inline code (`code`)
    text = re.sub(r'`([^`]+)`', r'<code style="background-color: #3d3d3d; padding: 2px 4px; border-radius: 3px;">\1</code>', text)

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
        self._stages: list[str] = []
        self._current_stage = -1
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
        self._stages = ["User"]
        self._current_stage = 0
        self._update_display()
        self.setVisible(True)

    def add_stage(self, name: str):
        """Add a new stage to the timeline."""
        self._stages.append(name)
        self._current_stage = len(self._stages) - 1
        self._update_display()

    def complete(self):
        """Mark the timeline as complete."""
        if "Done" not in self._stages:
            self._stages.append("Done")
        self._current_stage = len(self._stages) - 1
        self._update_display()

    def hide_timeline(self):
        """Hide the timeline."""
        self.setVisible(False)

    def _update_display(self):
        """Update the timeline display."""
        parts = []
        for i, stage in enumerate(self._stages):
            if i == self._current_stage:
                parts.append(f"<b style='color: #f59e0b;'>{stage}</b>")
            elif i < self._current_stage:
                parts.append(f"<span style='color: #22c55e;'>✓ {stage}</span>")
            else:
                parts.append(f"<span>{stage}</span>")

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
            self.message_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
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
            self.message_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

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


class ChatInputWidget(QPlainTextEdit):
    """Multi-line text input with Enter to send functionality."""

    message_submitted = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        """Set up the input widget UI."""
        colors = get_ida_colors()

        self.setPlaceholderText("Type a message... (Enter to send, Shift+Enter for new line)")
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
        """Handle Enter key to submit message."""
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            if event.modifiers() & Qt.ShiftModifier:
                # Shift+Enter: insert new line
                super().keyPressEvent(event)
            else:
                # Enter: submit message
                text = self.toPlainText().strip()
                if text:
                    self.message_submitted.emit(text)
                    self.clear()
        else:
            super().keyPressEvent(event)


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

    def __init__(self, db: Database, script_executor: Callable[[str], str], parent=None):
        super().__init__(parent)
        self.db = db
        self.script_executor = script_executor
        self.signals = AgentSignals()
        self.callback = PluginCallback(self.signals)
        self.core: IDAChatCore | None = None
        self._pending_message: str | None = None
        self._should_connect = False
        self._should_disconnect = False
        self._should_cancel = False
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
                self.core = IDAChatCore(
                    self.db,
                    self.callback,
                    script_executor=self.script_executor,
                )
                await self.core.connect()
                self.signals.connection_ready.emit()
            except Exception as e:
                self.signals.connection_error.emit(str(e))
                return

        # Process messages while running
        while self._running:
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
        self._create_ui()
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
            self.worker = AgentWorker(db, script_executor)

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

    def _on_connection_ready(self):
        """Called when agent connection is established."""
        self.chat_history.add_message("Agent connected and ready!", is_user=False)
        self.input_widget.setEnabled(True)

    def _on_connection_error(self, error: str):
        """Called when agent connection fails."""
        self.chat_history.add_message(f"Connection error: {error}", is_user=False)

    def _on_turn_start(self, turn: int, max_turns: int):
        """Called at the start of each agentic turn."""
        self._current_turn = turn
        self._max_turns = max_turns
        self.status_label.setText(f"Turn {turn}/{max_turns}")
        self.cancel_btn.setVisible(True)

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
        self.cancel_btn.setVisible(False)
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

        # Progress timeline (hidden by default)
        self.progress_timeline = ProgressTimeline()
        layout.addWidget(self.progress_timeline)

        # Chat history area (takes most space)
        self.chat_history = ChatHistoryWidget()
        layout.addWidget(self.chat_history, stretch=1)

        # Input area at bottom
        input_container = QWidget()
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(8)

        # Text input (Enter to send)
        self.input_widget = ChatInputWidget()
        self.input_widget.message_submitted.connect(self._on_message_submitted)
        input_layout.addWidget(self.input_widget, stretch=1)

        # Cancel button (hidden by default)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: #dc2626;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 6px 12px;
            }}
            QPushButton:hover {{
                background-color: #b91c1c;
            }}
        """)
        self.cancel_btn.clicked.connect(self._on_cancel)
        self.cancel_btn.setVisible(False)
        input_layout.addWidget(self.cancel_btn)

        layout.addWidget(input_container)

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
        self._add_welcome_message()

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
                options=ida_kernwin.PluginForm.WOPN_PERSIST
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
