"""
IDA Chat - Qt UI module.

All Qt / ida_domain imports and the dockable chat UI live here. This module is
imported lazily by ``ida_chat_plugin`` only after it confirms an interactive
GUI, so a headless load (idalib / idat) never imports Qt at all.

The UI deliberately uses plain, unstyled Qt widgets so it inherits IDA's
palette/theme. The conversation is a read-only QTextBrowser rendered from
Markdown (Qt renders GitHub-flavored Markdown natively), rather than a set of
hand-styled bubbles.
"""

import asyncio
import os
import re
import sys

# Signal to core that we're running inside IDA Pro (enables UI interaction API)
os.environ["IDA_CHAT_INSIDE_IDA"] = "1"
from pathlib import Path

import ida_idaapi
import ida_kernwin
import ida_loader
import ida_nalt
import ida_settings
from ida_domain import Database
from PySide6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QWidget,
    QFrame,
    QSizePolicy,
    QPlainTextEdit,
    QRadioButton,
    QButtonGroup,
    QLineEdit,
    QComboBox,
    QTextBrowser,
    QToolBar,
)
from PySide6.QtCore import Qt, Signal, QThread, QObject, QTimer
from PySide6.QtGui import QKeyEvent, QPixmap

# Ensure local modules are importable
sys.path.insert(0, str(Path(__file__).parent.resolve()))

from ida_chat_core import IDAChatCore, ChatCallback, test_claude_connection
from ida_chat_history import MessageHistory


# Model picker options: (display label, model alias passed to the SDK).
# None means "use Claude Code's configured default".
MODEL_CHOICES = [
    ("Default", None),
    ("Sonnet", "sonnet"),
    ("Opus", "opus"),
    ("Haiku", "haiku"),
]

# Reasoning effort levels exposed by the Agent SDK (ClaudeAgentOptions.effort).
# Guides how much thinking the model does. None = model default.
EFFORT_CHOICES = [
    ("Effort: Default", None),
    ("Effort: Low", "low"),
    ("Effort: Medium", "medium"),
    ("Effort: High", "high"),
    ("Effort: XHigh", "xhigh"),
    ("Effort: Max", "max"),
]


def _md_code(code: str, lang: str = "") -> str:
    """Wrap text in a Markdown fenced code block, robust to backticks inside.

    Chooses a fence longer than the longest backtick run in the content so the
    block can't be terminated early.
    """
    code = code.rstrip("\n")
    longest = run = 0
    for ch in code:
        if ch == "`":
            run += 1
            longest = max(longest, run)
        else:
            run = 0
    fence = "`" * max(3, longest + 1)
    return f"{fence}{lang}\n{code}\n{fence}"


_TABLE_DELIM = re.compile(r"^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)+\|?\s*$")


def _ensure_table_blank_lines(md: str) -> str:
    """Insert a blank line before a Markdown table when one is missing.

    Qt's Markdown parser only recognizes a table when a blank line precedes it
    (a bold lead-in like ``**Layout**`` directly above the header row otherwise
    collapses the whole thing into one paragraph). Models routinely omit that
    blank line, so we add it before any header row that is followed by a
    delimiter row (``|---|---|``).
    """
    lines = md.split("\n")
    out: list[str] = []
    for i, line in enumerate(lines):
        starts_table = (
            "|" in line
            and i + 1 < len(lines)
            and _TABLE_DELIM.match(lines[i + 1]) is not None
        )
        if starts_table and out and out[-1].strip():
            out.append("")
        out.append(line)
    return "\n".join(out)


def _blockquote(md: str) -> str:
    """Prefix every line (including blanks) with '> ' to indent a block.

    Every line must be prefixed — an unprefixed blank line would end the
    blockquote and split a fenced code block in two. Qt strips the '> ' markers,
    so code inside stays copy-paste clean and keeps its own indentation.
    """
    return "\n".join("> " + line for line in md.split("\n"))


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


# -----------------------------------------------------------------------------
# Conversation view
# -----------------------------------------------------------------------------


class TranscriptView(QTextBrowser):
    """Read-only conversation rendered from Markdown.

    Vanilla QTextBrowser: it inherits IDA's palette and renders GitHub-flavored
    Markdown (headings, bold, code fences, lists, links) with no custom styling.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(True)
        self._blocks: list[str] = []

    def clear_transcript(self) -> None:
        self._blocks = []
        self.setMarkdown("")

    def add_markdown(self, markdown: str) -> None:
        """Append a Markdown block and re-render."""
        markdown = markdown.strip()
        if not markdown:
            return
        self._blocks.append(markdown)
        self.setMarkdown("\n\n".join(self._blocks))
        QTimer.singleShot(0, self._scroll_to_bottom)

    def add_code(self, code: str, lang: str = "") -> None:
        self.add_markdown(_md_code(code, lang))

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())


class ChatInputWidget(QPlainTextEdit):
    """Multi-line text input with Enter to send and history navigation."""

    message_submitted = Signal(str)
    cancel_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[str] = []
        self._history_index = -1  # -1 means not browsing history
        self._current_input = ""  # Stores current input when browsing history
        self.setPlaceholderText("Type a message...  (↑↓ history, Enter send, Esc cancel)")
        self.setMaximumHeight(100)
        self.setMinimumHeight(40)

    def set_history(self, messages: list[str]):
        """Set the message history for up/down navigation (oldest first)."""
        self._history = list(messages)
        self._history_index = -1

    def add_to_history(self, message: str):
        """Add a message to the history."""
        if not self._history or self._history[-1] != message:
            self._history.append(message)
        self._history_index = -1

    def keyPressEvent(self, event: QKeyEvent):
        """Handle special keys: Enter, Escape, Up/Down for history."""
        key = event.key()
        if key == Qt.Key_Escape:
            self.cancel_requested.emit()
        elif key == Qt.Key_Up:
            self._navigate_history(-1)
        elif key == Qt.Key_Down:
            self._navigate_history(1)
        elif key in (Qt.Key_Return, Qt.Key_Enter):
            # Compare via .value to avoid the PyQt5-shim bitwise warning.
            shift = bool(
                event.modifiers().value & Qt.KeyboardModifier.ShiftModifier.value
            )
            if shift:
                super().keyPressEvent(event)  # Shift+Enter: newline
            else:
                text = self.toPlainText().strip()
                if text:
                    self.add_to_history(text)
                    self.message_submitted.emit(text)
                    self.clear()
                    self._history_index = -1
                    self.setFocus()
        else:
            super().keyPressEvent(event)

    def _navigate_history(self, direction: int):
        """Navigate through message history (-1 older, +1 newer)."""
        if not self._history:
            return

        if self._history_index == -1:
            self._current_input = self.toPlainText()

        if direction < 0:  # older
            if self._history_index == -1:
                new_index = len(self._history) - 1
            else:
                new_index = max(0, self._history_index - 1)
        else:  # newer
            if self._history_index == -1:
                return
            new_index = self._history_index + 1
            if new_index >= len(self._history):
                self._history_index = -1
                self.setPlainText(self._current_input)
                cursor = self.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                self.setTextCursor(cursor)
                return

        self._history_index = new_index
        self.setPlainText(self._history[self._history_index])
        cursor = self.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.setTextCursor(cursor)


# -----------------------------------------------------------------------------
# Agent worker (background thread running the async agent)
# -----------------------------------------------------------------------------


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

    def on_thinking_text(self, text: str) -> None:
        self.signals.thinking_text.emit(text)

    def on_tool_use(self, tool_name: str, details: str) -> None:
        self.signals.tool_use.emit(tool_name, details)

    def on_text(self, text: str, is_final: bool = True) -> None:
        self.signals.text.emit(text, is_final)

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
    thinking_text = Signal(str)
    tool_use = Signal(str, str)
    text = Signal(str, bool)  # (text, is_final)
    script_code = Signal(str)
    script_output = Signal(str)
    error = Signal(str)
    result = Signal(int, float)
    finished = Signal()
    connection_ready = Signal()
    connection_error = Signal(str)


class AgentWorker(QThread):
    """Background worker for running async agent calls."""

    def __init__(self, db_path: str, history: MessageHistory,
                 model: str | None = None, effort: str | None = None,
                 parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.history = history
        self.model = model
        self.effort = effort
        self.signals = AgentSignals()
        self.callback = PluginCallback(self.signals)
        self.core: IDAChatCore | None = None
        self._pending_message: str | None = None
        self._should_connect = False
        self._should_disconnect = False
        self._should_cancel = False
        self._should_new_session = False
        self._pending_model: str | None = None
        self._should_set_model = False
        self._running = True
        self._loop: asyncio.AbstractEventLoop | None = None

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
        """Request cancellation of current operation.

        Sets the in-loop cancel flag AND schedules an out-of-band interrupt on
        the worker's event loop, so Stop works even while the agent is blocked
        waiting out API retries (an outage), when the in-loop check never runs.
        """
        self._should_cancel = True
        if self.core:
            self.core.request_cancel()
            loop = getattr(self, "_loop", None)
            if loop is not None:
                try:
                    asyncio.run_coroutine_threadsafe(self.core.interrupt(), loop)
                except Exception:
                    pass

    def request_new_session(self):
        """Request starting a new session for history tracking."""
        self._should_new_session = True

    def request_set_model(self, model: str | None):
        """Request switching the model on the agent (applied on the worker loop).

        Only meaningful once connected; before that the initial model is taken
        from the picker when the worker is constructed.
        """
        self.model = model
        self._pending_model = model
        self._should_set_model = True

    def send_message(self, message: str):
        """Queue a message to be sent to the agent."""
        self._pending_message = message
        if not self.isRunning():
            self.start()

    def run(self):
        """Run the async event loop in this thread."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Exposed so request_cancel() (UI thread) can schedule an interrupt here.
        self._loop = loop

        try:
            loop.run_until_complete(self._async_run())
        finally:
            self._loop = None
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
                    self.db_path,
                    self.callback,
                    history=self.history,
                    model=self.model,
                    effort=self.effort,
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

            # Handle a model switch request
            if self._should_set_model and self.core:
                self._should_set_model = False
                try:
                    await self.core.set_model(self._pending_model)
                except Exception as e:
                    self.signals.error.emit(f"Could not switch model: {e}")

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


# -----------------------------------------------------------------------------
# Onboarding / settings panel
# -----------------------------------------------------------------------------


class OnboardingPanel(QFrame):
    """Onboarding panel for first-time setup and settings configuration."""

    onboarding_complete = Signal()  # Emitted when user clicks Save & Start

    def __init__(self, parent=None):
        super().__init__(parent)
        self._test_worker: TestConnectionWorker | None = None
        self._setup_ui()

    def _setup_ui(self):
        # Two columns: splash image (left) and settings (right).
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

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
                Qt.SmoothTransformation,
            ))
        image_label.setAlignment(Qt.AlignCenter)
        image_layout.addWidget(image_label)
        image_layout.addStretch()

        main_layout.addWidget(image_container, stretch=30)

        settings_container = QWidget()
        layout = QVBoxLayout(settings_container)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)

        # Title (larger, bold - via font, not a stylesheet color).
        title = QLabel("Welcome to IDA Chat")
        title_font = title.font()
        title_font.setPointSize(title_font.pointSize() + 5)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        instructions = QLabel("Configure your Claude authentication:")
        layout.addWidget(instructions)

        # Radio buttons for auth type
        self.auth_group = QButtonGroup(self)

        self.radio_system = QRadioButton("Use Claude settings on my machine")
        self.radio_system.setChecked(True)
        self.auth_group.addButton(self.radio_system, 0)
        layout.addWidget(self.radio_system)

        system_hint = QLabel("Recommended if Claude Code is installed")
        hint_font = system_hint.font()
        hint_font.setPointSize(max(1, hint_font.pointSize() - 1))
        system_hint.setFont(hint_font)
        system_hint.setIndent(20)
        layout.addWidget(system_hint)

        self.radio_oauth = QRadioButton("Claude account (Pro, Max, Team, or Enterprise)")
        self.auth_group.addButton(self.radio_oauth, 1)
        layout.addWidget(self.radio_oauth)

        self.radio_api_key = QRadioButton("Anthropic Console account (API billing)")
        self.auth_group.addButton(self.radio_api_key, 2)
        layout.addWidget(self.radio_api_key)

        # Key input (hidden for the system option)
        self.key_input = QLineEdit()
        self.key_input.setPlaceholderText("Paste your key here...")
        self.key_input.setEchoMode(QLineEdit.Password)
        self.key_input.hide()
        layout.addWidget(self.key_input)

        self.auth_group.buttonClicked.connect(self._on_auth_type_changed)

        # Buttons row
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(12)

        self.test_btn = QPushButton("Test Connection")
        self.test_btn.clicked.connect(self._on_test_clicked)
        buttons_layout.addWidget(self.test_btn)

        self.save_btn = QPushButton("Save && Start")
        self.save_btn.setDefault(True)
        self.save_btn.clicked.connect(self._on_save_clicked)
        buttons_layout.addWidget(self.save_btn)

        buttons_layout.addStretch()
        layout.addLayout(buttons_layout)

        self.status_label = QLabel("Not configured")
        layout.addWidget(self.status_label)

        # Response area (shows the joke on a successful test)
        self.response_label = QLabel()
        self.response_label.setWordWrap(True)
        self.response_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.response_label.hide()
        layout.addWidget(self.response_label)

        layout.addStretch()

        main_layout.addWidget(settings_container, stretch=70)

    def _on_auth_type_changed(self, button):
        """Show/hide key input based on selected auth type."""
        self.key_input.setVisible(button is not self.radio_system)

    def _on_test_clicked(self):
        """Run connection test."""
        self.test_btn.setEnabled(False)
        self.save_btn.setEnabled(False)
        self.status_label.setText("Testing connection...")
        self.response_label.hide()

        self._apply_current_settings()

        self._test_worker = TestConnectionWorker(self)
        self._test_worker.finished.connect(self._on_test_finished)
        self._test_worker.start()

    def _on_test_finished(self, success: bool, message: str):
        """Handle test result."""
        self.test_btn.setEnabled(True)
        self.save_btn.setEnabled(True)

        if success:
            self.status_label.setText("✓ Connected! You're all set.")
            self.response_label.setText(message)
            self.response_label.show()
        else:
            self.status_label.setText(f"✗ Connection failed: {message}")
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
        return "api_key"

    def _on_save_clicked(self):
        """Save settings and emit completion signal."""
        auth_type = self._get_auth_type()
        api_key = self.key_input.text().strip() if auth_type != "system" else None

        if auth_type != "system" and not api_key:
            self.status_label.setText("Please enter your API key")
            return

        save_auth_settings(auth_type, api_key)
        self._apply_current_settings()
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

        self.status_label.setText("Settings loaded")
        self.response_label.hide()


def _nexus_gui_paths() -> tuple[str, str]:
    """Return (idb_path, exe_path) as canonical absolute paths for the open DB."""
    idb_path = ida_loader.get_path(ida_loader.PATH_TYPE_IDB) or ""
    exe_path = ida_nalt.get_input_file_path() or ""
    return (
        str(Path(idb_path).resolve()) if idb_path else "",
        str(Path(exe_path).resolve()) if exe_path else "",
    )


# -----------------------------------------------------------------------------
# Main chat form
# -----------------------------------------------------------------------------


class IDAChatForm(ida_kernwin.PluginForm):
    """Main chat widget form."""

    def OnCreate(self, form):
        """Called when the widget is created."""
        self.parent = self.FormToPyQtWidget(form)
        self.worker: AgentWorker | None = None
        self.history: MessageHistory | None = None
        self._is_processing = False
        self._current_turn = 0
        self._max_turns = 20
        self._total_cost = 0.0
        self._message_count = 0
        # Tracks the last rendered block's role so we only print the "Assistant"
        # header when the agent's prose starts (not before every text chunk).
        self._last_role: str | None = None

        # Allow horizontal resizing (IDA remembers preferred size)
        self.parent.setMinimumWidth(500)
        self.parent.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        self._create_ui()

        # Apply saved auth settings to environment
        apply_auth_to_environment()

        if get_show_wizard():
            self._show_onboarding()
        else:
            self._init_agent()

    # -- UI construction ------------------------------------------------------

    def _create_ui(self):
        """Create the chat interface UI (plain, unstyled widgets)."""
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Header toolbar (vanilla actions, standard theming)
        toolbar = QToolBar()
        toolbar.setMovable(False)
        act_settings = toolbar.addAction("Settings")
        act_settings.setToolTip("Configure authentication")
        act_settings.triggered.connect(self._show_settings)
        act_export = toolbar.addAction("Export")
        act_export.setToolTip("Export chat as HTML")
        act_export.triggered.connect(self._on_share)
        act_clear = toolbar.addAction("Clear")
        act_clear.setToolTip("Clear chat")
        act_clear.triggered.connect(self._on_clear)
        layout.addWidget(toolbar)

        # Onboarding panel (shown on first launch or when Settings is clicked)
        self.onboarding_panel = OnboardingPanel()
        self.onboarding_panel.onboarding_complete.connect(self._on_onboarding_complete)
        self.onboarding_panel.hide()
        layout.addWidget(self.onboarding_panel)

        # Conversation
        self.transcript = TranscriptView()
        layout.addWidget(self.transcript, stretch=1)

        # Input area: multi-line text box (Send/Stop lives in the status bar).
        self.input_container = QWidget()
        input_layout = QHBoxLayout(self.input_container)
        input_layout.setContentsMargins(6, 6, 6, 6)
        input_layout.setSpacing(6)
        self.input_widget = ChatInputWidget()
        self.input_widget.message_submitted.connect(self._on_message_submitted)
        self.input_widget.cancel_requested.connect(self._on_cancel)
        input_layout.addWidget(self.input_widget, stretch=1)
        layout.addWidget(self.input_container)

        # Status bar: model picker + effort picker + status text + Send/Stop
        self.status_bar = QWidget()
        status_layout = QHBoxLayout(self.status_bar)
        status_layout.setContentsMargins(8, 2, 8, 2)
        status_layout.setSpacing(6)

        self.model_combo = QComboBox()
        for label, model_id in MODEL_CHOICES:
            self.model_combo.addItem(label, model_id)
        self.model_combo.setCurrentIndex(0)
        self.model_combo.setToolTip("Model")
        # Connect after populating so setup doesn't fire the handler.
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        status_layout.addWidget(self.model_combo)

        self.effort_combo = QComboBox()
        for label, effort_id in EFFORT_CHOICES:
            self.effort_combo.addItem(label, effort_id)
        self.effort_combo.setCurrentIndex(0)
        self.effort_combo.setToolTip(
            "Reasoning effort — guides how much the model thinks. "
            "Changing it restarts the session."
        )
        self.effort_combo.currentIndexChanged.connect(self._on_effort_changed)
        status_layout.addWidget(self.effort_combo)

        self.status_label = QLabel("")
        status_layout.addWidget(self.status_label)
        status_layout.addStretch()

        # Send/Stop on the same line as the pickers, right-aligned.
        self.send_button = QPushButton("Send")
        self.send_button.setToolTip("Send message (Enter) — becomes Stop while working")
        self.send_button.setMinimumWidth(72)
        self.send_button.clicked.connect(self._on_send_or_stop)
        status_layout.addWidget(self.send_button)

        layout.addWidget(self.status_bar)

        self.parent.setLayout(layout)

        self._add_welcome_message()

    def _add_welcome_message(self):
        self.transcript.add_markdown("Welcome to **IDA Chat**. Connecting to Claude…")
        self.input_widget.setEnabled(False)
        self.send_button.setEnabled(False)

    # -- Agent lifecycle ------------------------------------------------------

    def _init_agent(self):
        """Initialize the agent worker against the plugin-registered GUI IDB."""
        # Tear down any previous worker (e.g. re-entering from Settings) so we
        # do not leak connections.
        if self.worker:
            self.worker.request_disconnect()
            self.worker.wait(5000)
            self.worker = None

        try:
            idb_path, _exe_path = _nexus_gui_paths()
            if not idb_path:
                idb_path = Database.open().path

            self.history = MessageHistory(idb_path)
            self.worker = AgentWorker(
                idb_path,
                self.history,
                model=self.model_combo.currentData(),
                effort=self.effort_combo.currentData(),
            )

            self.worker.signals.connection_ready.connect(self._on_connection_ready)
            self.worker.signals.connection_error.connect(self._on_connection_error)
            self.worker.signals.turn_start.connect(self._on_turn_start)
            self.worker.signals.thinking.connect(self._on_thinking)
            self.worker.signals.thinking_done.connect(self._on_thinking_done)
            self.worker.signals.thinking_text.connect(self._on_thinking_text)
            self.worker.signals.tool_use.connect(self._on_tool_use)
            self.worker.signals.text.connect(self._on_text)
            self.worker.signals.script_code.connect(self._on_script_code)
            self.worker.signals.script_output.connect(self._on_script_output)
            self.worker.signals.error.connect(self._on_error)
            self.worker.signals.result.connect(self._on_result)
            self.worker.signals.finished.connect(self._on_finished)

            self.worker.request_connect()
        except Exception as e:
            self.transcript.add_markdown(f"**Error initializing agent:** {e}")

    def _show_onboarding(self):
        """Show onboarding panel, hide chat UI."""
        self.onboarding_panel.show()
        self.transcript.hide()
        self.input_container.hide()
        self.status_bar.hide()

    def _show_settings(self):
        """Show settings panel (re-uses the onboarding panel)."""
        self.onboarding_panel.load_current_settings()
        self._show_onboarding()

    def _on_onboarding_complete(self):
        """Handle successful onboarding."""
        self.onboarding_panel.hide()
        self.transcript.show()
        self.input_container.show()
        self.status_bar.show()
        self._init_agent()

    # -- Status / model -------------------------------------------------------

    def _update_status_bar(self, processing_text: str | None = None):
        """Update the status text (model is shown by the picker)."""
        if processing_text:
            self.status_label.setText(processing_text)
        else:
            parts = [f"{self._message_count} msgs"]
            if self._total_cost > 0:
                parts.append(f"${self._total_cost:.4f}")
            self.status_label.setText("  ·  ".join(parts))

    def _set_processing(self, processing: bool):
        """Reflect processing state in the Send/Stop button and input box."""
        self._is_processing = processing
        self.send_button.setText("Stop" if processing else "Send")
        self.send_button.setToolTip(
            "Stop the current response" if processing else "Send message (Enter)"
        )
        # Input is disabled while working; the button stays live so it can Stop.
        self.input_widget.setEnabled(not processing)
        self.send_button.setEnabled(True)
        if not processing:
            self.input_widget.setFocus()

    def _on_model_changed(self, index: int):
        """Switch the agent model from the picker."""
        model_id = self.model_combo.itemData(index)
        if self.worker:
            self.worker.request_set_model(model_id)

    def _on_effort_changed(self, index: int):
        """Change reasoning effort.

        The SDK has no live effort setter (unlike the model), so this reconnects
        the agent with the new effort. That starts a fresh session; the picker's
        value is read at connect time, so before the first connection this is a
        no-op beyond storing the selection.
        """
        if not self.worker:
            return
        label = self.effort_combo.itemText(index)
        self.transcript.add_markdown(
            f"_Switched to {label.lower()} — restarting the session._"
        )
        self._init_agent()

    # -- Agent signal handlers ------------------------------------------------

    def _on_connection_ready(self):
        self.transcript.add_markdown("_Agent connected and ready._")
        self._set_processing(False)
        self._update_status_bar()
        if self.history:
            self.input_widget.set_history(self.history.get_all_user_messages())

    def _on_connection_error(self, error: str):
        self.transcript.add_markdown(f"**Connection error:** {error}")

    def _on_turn_start(self, turn: int, max_turns: int):
        self._current_turn = turn
        self._max_turns = max_turns

    def _on_thinking(self):
        self._set_processing(True)
        self._update_status_bar("Thinking…")

    def _on_thinking_done(self):
        self._update_status_bar("Working…")

    def _on_thinking_text(self, text: str):
        """Render the model's reasoning as a muted block, distinct from its answer."""
        text = text.strip()
        if not text:
            return
        self._last_role = "activity"
        quoted = "\n".join(f"> {ln}" for ln in text.splitlines())
        self.transcript.add_markdown(f"> **Thinking**\n>\n{quoted}")

    def _on_tool_use(self, tool_name: str, details: str):
        self._last_role = "activity"
        line = f"**{tool_name}**"
        if details:
            line += f" — `{details}`"
        self.transcript.add_markdown(f"> {line}")

    def _on_text(self, text: str, is_final: bool = True):
        text = text.strip()
        if not text:
            return
        if is_final:
            # The turn's concluding answer: a clear "Assistant" header (once per
            # contiguous run) and normalized tables so Qt renders them.
            if self._last_role != "assistant":
                self.transcript.add_markdown("### Assistant")
            self._last_role = "assistant"
            self.transcript.add_markdown(_ensure_table_blank_lines(text))
        else:
            # Intermediate narration before a tool call: muted, quoted, so it
            # reads as running commentary rather than the final answer.
            self._last_role = "activity"
            self.transcript.add_markdown(_blockquote(text))

    def _on_script_code(self, code: str):
        # Code the agent runs against the database (input to IDA); indented.
        self._last_role = "activity"
        block = "**Running Python**\n\n" + _md_code(code, "python")
        self.transcript.add_markdown(_blockquote(block))

    def _on_script_output(self, output: str):
        # Result of running that code (output from IDA); indented.
        if output.strip():
            self._last_role = "activity"
            block = "**Output**\n\n" + _md_code(output)
            self.transcript.add_markdown(_blockquote(block))

    def _on_error(self, error: str):
        # Render errors (API outages, cancellations, ...) as a clear, quoted
        # block set apart from the conversation. No emoji.
        self._last_role = "activity"
        self.transcript.add_markdown(_blockquote(f"**Error**\n\n{error}"))

    def _on_result(self, _num_turns: int, cost: float):
        self._total_cost += cost

    def _on_finished(self):
        self._message_count += 1
        self._set_processing(False)
        self._update_status_bar()

    # -- Input / actions ------------------------------------------------------

    def _on_message_submitted(self, text: str):
        self._send_message(text)

    def _on_send_or_stop(self):
        """Send button: submit when idle, cancel when working."""
        if self._is_processing:
            self._on_cancel()
            return
        text = self.input_widget.toPlainText().strip()
        if text:
            self.input_widget.add_to_history(text)
            self.input_widget.clear()
            self._send_message(text)

    def _send_message(self, text: str):
        if not self.worker or self._is_processing:
            return
        # Separator + clear "User" header for the new turn.
        self._last_role = "user"
        self.transcript.add_markdown(f"---\n\n### User\n\n{text}")
        self.worker.send_message(text)

    def _on_cancel(self):
        if self.worker and self._is_processing:
            self.worker.request_cancel()

    def _on_share(self):
        """Export the current chat session as HTML."""
        from ida_chat_core import export_transcript

        if not self.history:
            self.transcript.add_markdown("_No active session to export._")
            return

        session_file = self.history.session_file
        if not session_file or not session_file.exists():
            self.transcript.add_markdown("_No session file found to export._")
            return

        idb_path = Path(self.history.binary_path)
        html_path = idb_path.parent / (idb_path.stem + "_chat.html")

        try:
            export_transcript(session_file, html_path)
            file_url = html_path.resolve().as_uri()
            self.transcript.add_markdown(f"Chat exported to: [{html_path}]({file_url})")
        except Exception as e:
            self.transcript.add_markdown(f"**Export failed:** {e}")

    def _on_clear(self):
        """Clear the chat transcript and start a new session."""
        self.transcript.clear_transcript()
        self._total_cost = 0.0
        self._message_count = 0

        if self.worker:
            self.worker.request_new_session()

        self.transcript.add_markdown("_Chat cleared. Ready for a new conversation._")
        self._set_processing(False)
        self._update_status_bar()

    def OnClose(self, form):
        """Called when the widget is closed."""
        if self.worker:
            self.worker.request_disconnect()
            self.worker.wait(5000)
            self.worker = None
