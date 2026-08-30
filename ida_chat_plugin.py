"""
IDA Chat - LLM Chat Client Plugin for IDA Pro (entry point).

This module is the plugin entry IDA loads. It is intentionally free of any Qt /
ida_domain imports so it is safe to load headless (idalib / idat), which emit
Qt/PySide6 warnings and have no display. The Qt UI lives in ``ida_chat_ui`` and
is imported lazily only after ``init()`` confirms an interactive GUI, mirroring
the ida-nexus plugin's headless back-off.
"""

import os
import sys
from pathlib import Path

import ida_idaapi
import ida_kernwin

# Ensure sibling modules (ida_chat_ui, ida_chat_core, ...) are importable when
# IDA loads this plugin by path.
sys.path.insert(0, str(Path(__file__).parent.resolve()))


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


def is_interactive_gui() -> bool:
    """True only for the Qt GUI, never idat or idalib.

    idalib's UI compatibility shim can still report ``is_idaq()``, so we also
    require the interactive-session env var — the same check the ida-nexus
    plugin uses to back off in headless mode.
    """
    return bool(
        ida_kernwin.is_idaq() and os.environ.get("IDA_IS_INTERACTIVE") == "1"
    )


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
        """Initialize the plugin.

        Declines (without importing Qt) when running headless — idalib / idat —
        so a batch run never pulls in PySide6 or its warnings. The Qt UI is only
        imported later, lazily, in toggle_widget().
        """
        self.form = None
        self._nexus_initialized = False

        if not is_interactive_gui():
            return ida_idaapi.PLUGIN_SKIP

        action_desc = ida_kernwin.action_desc_t(
            ACTION_ID,
            ACTION_NAME,
            ToggleWidgetHandler(self),
            ACTION_HOTKEY,
            ACTION_TOOLTIP,
            -1,
        )

        if not ida_kernwin.register_action(action_desc):
            ida_kernwin.msg(f"{PLUGIN_NAME}: Failed to register action\n")
            return ida_idaapi.PLUGIN_SKIP

        ida_kernwin.attach_action_to_menu(
            "View/",
            ACTION_ID,
            ida_kernwin.SETMENU_APP,
        )

        try:
            import ida_nexus.plugin

            self._nexus_initialized = ida_nexus.plugin.init(owner="ida-chat")
        except Exception as exc:  # IDA startup may raise SWIG errors
            ida_kernwin.msg(f"[ida-chat] failed to initialize IDA Nexus: {exc}\n")
        if not self._nexus_initialized:
            ida_kernwin.detach_action_from_menu("View/", ACTION_ID)
            ida_kernwin.unregister_action(ACTION_ID)
            return ida_idaapi.PLUGIN_SKIP

        ida_kernwin.msg(f"{PLUGIN_NAME}: Loaded (use {ACTION_HOTKEY} to toggle)\n")
        return ida_idaapi.PLUGIN_KEEP

    def toggle_widget(self):
        """Show or hide the dockable widget.

        The Qt UI module is imported here (not at module load) so Qt is only ever
        touched in an interactive GUI session.
        """
        widget = ida_kernwin.find_widget(WIDGET_TITLE)

        if widget:
            ida_kernwin.close_widget(widget, 0)
            self.form = None
        else:
            from ida_chat_ui import IDAChatForm  # lazy: Qt only in the GUI

            self.form = IDAChatForm()
            self.form.Show(
                WIDGET_TITLE,
                options=(
                    ida_kernwin.PluginForm.WOPN_PERSIST |
                    ida_kernwin.PluginForm.WOPN_DP_RIGHT |
                    ida_kernwin.PluginForm.WOPN_DP_SZHINT
                )
            )
            ida_kernwin.set_dock_pos(
                WIDGET_TITLE,
                'IDATopLevelDockArea',
                ida_kernwin.DP_RIGHT | ida_kernwin.DP_SZHINT
            )

    def run(self, arg):
        """Called when plugin is invoked directly."""
        self.toggle_widget()

    def term(self):
        """Clean up when plugin is unloaded.

        term() runs even when init() declined with PLUGIN_SKIP, so bail out early
        in headless mode where we never registered anything (and Qt was never
        imported).
        """
        if not is_interactive_gui():
            return

        widget = ida_kernwin.find_widget(WIDGET_TITLE)
        if widget:
            ida_kernwin.close_widget(widget, 0)

        ida_kernwin.detach_action_from_menu("View/", ACTION_ID)
        ida_kernwin.unregister_action(ACTION_ID)

        if self._nexus_initialized:
            import ida_nexus.plugin

            ida_nexus.plugin.term()
            self._nexus_initialized = False

        ida_kernwin.msg(f"{PLUGIN_NAME}: Unloaded\n")


def PLUGIN_ENTRY():
    """Plugin entry point."""
    return IDAChatPlugin()
