# IDA User Interaction API

**This API is only available when running inside IDA Pro.**

Use these native IDA Python methods (`ida_kernwin`) to interact with the user's current UI state.

- **Use `ida_kernwin` (this file)**: ONLY for interacting with the user's cursor, selection, and navigation
- You are allowed to mix these snippets with IDA Domain snippets. All the techniques below imply a DB is open so you
  don't need to worry about that

## When to Use These Methods

- **"current function"**, **"where I am"**, **"this address"** → Use `get_screen_ea()` to get the user's cursor position
- **"go to"**, **"jump to"**, **"navigate to"** → Use `jumpto()` to move the cursor
- **"selected code"**, **"this range"**, **"selection"** → Use `read_range_selection()` to get selected addresses
- **"highlighted"**, **"this identifier"** → Use `get_highlight()` to get the highlighted text

## Getting Current Position

| Function                         | Module        | Description                                     |
|----------------------------------|---------------|-------------------------------------------------|
| `get_screen_ea()`                | `ida_kernwin` | Returns address at cursor position              |
| `idc.here()` or `idc.ScreenEA()` | `idc`         | Legacy aliases for current address              |
| `get_cursor()`                   | `ida_kernwin` | Returns (x, y) cursor coordinates               |
| `get_curline()`                  | `ida_kernwin` | Gets current disassembly line                   |
| `get_highlight(view, flags=0)`   | `ida_kernwin` | Returns highlighted identifier as (text, flags) |

## Navigation / Jumping

| Function                            | Signature                                          | Description                 |
|-------------------------------------|----------------------------------------------------|-----------------------------|
| `jumpto()`                          | `jumpto(ea, opnum=-1, uijmp_flags=UIJMP_ACTIVATE)` | Jump to address             |
| `jumpto()`                          | `jumpto(custom_viewer, place, x, y)`               | Set cursor in custom viewer |
| `ea_viewer_history_push_and_jump()` | `(view, ea, x, y, lnnum)`                          | Push history and jump       |

## Selection

| Function                       | Description                          |
|--------------------------------|--------------------------------------|
| `read_selection(view, p1, p2)` | Get selection as position objects    |
| `read_range_selection(view)`   | Returns (ok, start_ea, end_ea) tuple |

## Code Samples

### Get Current Cursor Address

```python
import ida_kernwin

# Get the address where the user's cursor is positioned
ea = ida_kernwin.get_screen_ea()
print(f"Current address: 0x{ea:X}")

# Get the function containing this address
func = ida_funcs.get_func(ea)
if func:
    name = ida_funcs.get_func_name(func.start_ea)
    print(f"Current function: {name}")
```

### Get Current Line Content

```python
import ida_kernwin

# Get the current disassembly line (includes color codes)
line = ida_kernwin.get_curline()

# Strip color codes to get plain text
import ida_lines

clean_line = ida_lines.tag_remove(line)
print(f"Current line: {clean_line}")
```

### Get Highlighted Identifier

```python
import ida_kernwin

# Get the identifier currently highlighted/selected by the user
# Pass None for the current view
result = ida_kernwin.get_highlight(ida_kernwin.get_current_widget())
if result:
    text, flags = result
    print(f"Highlighted: {text}")
else:
    print("Nothing highlighted")
```

### Jump to Address

```python
import ida_kernwin

# Jump to a specific address
target_ea = 0x401000
success = ida_kernwin.jumpto(target_ea)
if success:
    print(f"Jumped to 0x{target_ea:X}")
else:
    print("Jump failed")
```

### Jump to Function by Name

```python
import ida_kernwin
import ida_name

# Find address by name and jump to it
name = "main"
ea = ida_name.get_name_ea(ida_idaapi.BADADDR, name)
if ea != ida_idaapi.BADADDR:
    ida_kernwin.jumpto(ea)
    print(f"Jumped to {name} at 0x{ea:X}")
else:
    print(f"Function '{name}' not found")
```

### Get Selected Range

```python
import ida_kernwin

# Get the address range selected by the user
result = ida_kernwin.read_range_selection(None)  # None = current view
if result[0]:  # First element is success boolean
    start_ea = result[1]
    end_ea = result[2]
    print(f"Selection: 0x{start_ea:X} - 0x{end_ea:X}")
    print(f"Size: {end_ea - start_ea} bytes")
else:
    print("No selection")
```

### Iterate Over Selected Instructions

```python
import ida_kernwin
import ida_ua
import idautils

# Get selection and iterate over instructions
result = ida_kernwin.read_range_selection(None)
if result[0]:
    start_ea, end_ea = result[1], result[2]
    print(f"Instructions in selection 0x{start_ea:X} - 0x{end_ea:X}:")

    for head in idautils.Heads(start_ea, end_ea):
        insn = ida_ua.insn_t()
        if ida_ua.decode_insn(insn, head):
            print(f"  0x{head:X}: {ida_ua.print_insn_mnem(head)}")
else:
    print("No selection - select a range first")
```

### Get Cursor Coordinates

```python
import ida_kernwin

# Get x, y coordinates of cursor in the view
x, y = ida_kernwin.get_cursor()
print(f"Cursor position: column={x}, line={y}")
```

### Jump with History (Back Button Support)

```python
import ida_kernwin

# Jump to address while preserving navigation history
# This allows the user to press Esc to go back
view = ida_kernwin.get_current_widget()
target_ea = 0x401000

ida_kernwin.ea_viewer_history_push_and_jump(
    view,  # Current view
    target_ea,  # Destination address
    0,  # x coordinate (0 = default)
    0,  # y coordinate (0 = default)
    0  # line number (0 = default)
)
print(f"Jumped to 0x{target_ea:X} (Esc to go back)")
```

### Complete Example: Analyze Current Function

```python
import ida_kernwin
import ida_funcs
import ida_hexrays
import idautils

# Get current cursor position
ea = ida_kernwin.get_screen_ea()
func = ida_funcs.get_func(ea)

if func:
    name = ida_funcs.get_func_name(func.start_ea)
    size = func.end_ea - func.start_ea

    # Count instructions
    insn_count = sum(1 for _ in idautils.Heads(func.start_ea, func.end_ea))

    # Get callers
    callers = list(idautils.CodeRefsTo(func.start_ea, False))

    print(f"Function: {name}")
    print(f"Address: 0x{func.start_ea:X} - 0x{func.end_ea:X}")
    print(f"Size: {size} bytes, {insn_count} instructions")
    print(f"Called from {len(callers)} locations")
else:
    print("Cursor not in a function")
```

### Complete Example: Process Selected Range

```python
import ida_kernwin
import ida_bytes
import ida_ua
import idautils

# Get user's selection
result = ida_kernwin.read_range_selection(None)

if result[0]:
    start_ea, end_ea = result[1], result[2]

    # Collect statistics
    instructions = []
    data_refs = set()
    code_refs = set()

    for head in idautils.Heads(start_ea, end_ea):
        insn = ida_ua.insn_t()
        if ida_ua.decode_insn(insn, head):
            instructions.append(insn)

            # Collect references
            for ref in idautils.DataRefsFrom(head):
                data_refs.add(ref)
            for ref in idautils.CodeRefsFrom(head, False):
                if ref < start_ea or ref >= end_ea:
                    code_refs.add(ref)

    print(f"Selection analysis: 0x{start_ea:X} - 0x{end_ea:X}")
    print(f"  Instructions: {len(instructions)}")
    print(f"  Data references: {len(data_refs)}")
    print(f"  External calls: {len(code_refs)}")
else:
    # Fall back to current address
    ea = ida_kernwin.get_screen_ea()
    print(f"No selection, current address: 0x{ea:X}")
```
