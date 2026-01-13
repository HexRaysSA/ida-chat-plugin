"""
IDA Chat - LLM Chat Client Plugin for IDA Pro

A dockable chat interface that serves as a base for developing
a full LLM chat client within IDA Pro.
"""

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
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent, QPalette


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


class ChatMessage(QFrame):
    """A single chat message bubble."""

    def __init__(self, text: str, is_user: bool = True, parent=None):
        super().__init__(parent)
        self.is_user = is_user
        self._setup_ui(text)

    def _setup_ui(self, text: str):
        """Set up the message bubble UI."""
        colors = get_ida_colors()

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)

        # Create message label
        message_widget = QLabel(text)
        message_widget.setWordWrap(True)
        message_widget.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        message_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)

        if self.is_user:
            # User message - right aligned, accent color background
            layout.addStretch()
            message_widget.setStyleSheet(f"""
                QLabel {{
                    background-color: {colors['highlight']};
                    color: {colors['highlight_text']};
                    border-radius: 10px;
                    padding: 8px 12px;
                }}
            """)
            layout.addWidget(message_widget)
        else:
            # Assistant message - left aligned, alternate base color
            message_widget.setStyleSheet(f"""
                QLabel {{
                    background-color: {colors['alt_base']};
                    color: {colors['text']};
                    border-radius: 10px;
                    padding: 8px 12px;
                }}
            """)
            layout.addWidget(message_widget)
            layout.addStretch()


class ChatHistoryWidget(QScrollArea):
    """Scrollable chat history container."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        """Set up the chat history UI."""
        colors = get_ida_colors()

        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFrameShape(QFrame.NoFrame)

        # Container widget for messages
        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setSpacing(8)
        self.layout.setContentsMargins(8, 8, 8, 8)
        self.layout.addStretch()  # Push messages to top initially

        self.setWidget(self.container)

    def add_message(self, text: str, is_user: bool = True):
        """Add a message to the chat history."""
        # Remove the stretch before adding
        self.layout.takeAt(self.layout.count() - 1)

        # Add the message
        message = ChatMessage(text, is_user)
        self.layout.addWidget(message)

        # Re-add stretch at the end
        self.layout.addStretch()

        # Scroll to bottom
        self.scroll_to_bottom()

    def scroll_to_bottom(self):
        """Scroll the chat history to the bottom."""
        # Use a slight delay to ensure layout is updated
        from PySide6.QtCore import QTimer
        QTimer.singleShot(10, lambda: self.verticalScrollBar().setValue(
            self.verticalScrollBar().maximum()
        ))

    def clear_history(self):
        """Clear all messages from the chat history."""
        # Remove all widgets except the stretch
        while self.layout.count() > 1:
            item = self.layout.takeAt(0)
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


class IDAChatForm(ida_kernwin.PluginForm):
    """Main chat widget form."""

    def OnCreate(self, form):
        """Called when the widget is created."""
        self.parent = self.FormToPyQtWidget(form)
        self._create_ui()

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

        layout.addWidget(header)

        # Separator
        separator = QFrame()
        separator.setFrameShape(QFrame.HLine)
        separator.setStyleSheet(f"background-color: {colors['mid']};")
        separator.setFixedHeight(1)
        layout.addWidget(separator)

        # Chat history area (takes most space)
        self.chat_history = ChatHistoryWidget()
        layout.addWidget(self.chat_history, stretch=1)

        # Input area at bottom
        input_container = QWidget()
        input_layout = QHBoxLayout(input_container)
        input_layout.setContentsMargins(8, 8, 8, 8)
        input_layout.setSpacing(8)

        # Text input
        self.input_widget = ChatInputWidget()
        self.input_widget.message_submitted.connect(self._on_message_submitted)
        input_layout.addWidget(self.input_widget, stretch=1)

        # Send button
        send_btn = QPushButton("Send")
        send_btn.setStyleSheet(f"""
            QPushButton {{
                background-color: {colors['highlight']};
                color: {colors['highlight_text']};
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-weight: bold;
            }}
            QPushButton:hover {{
                background-color: {colors['dark']};
            }}
        """)
        send_btn.clicked.connect(self._on_send_clicked)
        input_layout.addWidget(send_btn)

        layout.addWidget(input_container)

        self.parent.setLayout(layout)

        # Add welcome message
        self._add_welcome_message()

    def _add_welcome_message(self):
        """Add a welcome message to the chat."""
        welcome_text = (
            "Welcome to IDA Chat! This is a base implementation for an LLM chat client. "
            "Connect your preferred LLM backend to enable AI-powered analysis."
        )
        self.chat_history.add_message(welcome_text, is_user=False)

    def _on_message_submitted(self, text: str):
        """Handle message submission from input widget."""
        self._send_message(text)

    def _on_send_clicked(self):
        """Handle send button click."""
        text = self.input_widget.toPlainText().strip()
        if text:
            self._send_message(text)
            self.input_widget.clear()

    def _send_message(self, text: str):
        """Send a message and get a response."""
        # Add user message to chat
        self.chat_history.add_message(text, is_user=True)

        # Check for built-in commands
        if text.strip().lower() == "list functions":
            response = self._handle_list_functions()
        else:
            # TODO: Replace this with actual LLM backend integration
            response = self._get_placeholder_response(text)

        self.chat_history.add_message(response, is_user=False)

    def _get_placeholder_response(self, user_message: str) -> str:
        """
        Get a placeholder response.

        TODO: Replace this method with actual LLM backend integration.
        Implement your LLM API calls here (e.g., OpenAI, Anthropic, local models).
        """
        return (
            f"This is a placeholder response. To enable AI responses, "
            f"integrate your LLM backend in the _send_message() method.\n\n"
            f"Your message was: \"{user_message[:50]}{'...' if len(user_message) > 50 else ''}\""
        )

    def _handle_list_functions(self) -> str:
        """List all functions in the current IDB using IDA Domain API."""
        try:
            db = Database.open()

            functions = []
            for func in db.functions:
                name = db.functions.get_name(func)
                start_ea = func.start_ea
                end_ea = func.end_ea
                size = end_ea - start_ea
                functions.append((name, start_ea, end_ea, size))

            if not functions:
                return "No functions found in the current database."

            lines = [f"Found {len(functions)} functions:\n"]

            MAX_DISPLAY = 50
            for name, start, end, size in functions[:MAX_DISPLAY]:
                lines.append(f"  {name}: 0x{start:08X} - 0x{end:08X} ({size} bytes)")

            if len(functions) > MAX_DISPLAY:
                lines.append(f"\n  ... and {len(functions) - MAX_DISPLAY} more functions")

            return "\n".join(lines)

        except Exception as e:
            return f"Error listing functions: {str(e)}"

    def _on_clear(self):
        """Clear the chat history."""
        self.chat_history.clear_history()
        self._add_welcome_message()

    def OnClose(self, form):
        """Called when the widget is closed."""
        pass


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
