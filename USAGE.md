# IDA Domain API Usage

This document describes how to write scripts for the IDA Chat plugin using the ida-domain API.

**For the complete API reference, see [API_REFERENCE.md](API_REFERENCE.md).**

## Overview

Scripts have access to an open IDA database via the `db` variable. Use `print()` to output results.

## Common Patterns

### List All Functions

```python
for func in db.functions:
    name = db.functions.get_name(func)
    size = func.end_ea - func.start_ea
    print(f"{name}: 0x{func.start_ea:08X} - 0x{func.end_ea:08X} ({size} bytes)")
```

### Find Function by Name

```python
func = db.functions.get_function_by_name("main")
if func:
    print(f"Found main at 0x{func.start_ea:08X}")

    # Get callers
    callers = db.functions.get_callers(func)
    print(f"Called by {len(callers)} functions:")
    for caller in callers:
        print(f"  - {db.functions.get_name(caller)}")
else:
    print("main not found")
```

### Search Strings

```python
import re

# Find all strings
for s in db.strings:
    print(f"0x{s.address:08X}: {s}")

# Find URLs
url_pattern = re.compile(r"https?://[\w./]+", re.IGNORECASE)
for s in db.strings:
    try:
        content = str(s)
        if url_pattern.search(content):
            print(f"URL found: {content}")
    except:
        pass
```

### Analyze Cross-References

```python
# Get xrefs TO an address
target = 0x00401000
print(f"References TO 0x{target:08X}:")
for xref in db.xrefs.to_ea(target):
    print(f"  From 0x{xref.from_ea:08X} (type: {xref.type.name})")

# Get xrefs FROM an address
print(f"References FROM 0x{target:08X}:")
for xref in db.xrefs.from_ea(target):
    print(f"  To 0x{xref.to_ea:08X} (type: {xref.type.name})")
```

### Decompile Function

```python
func = db.functions.get_function_by_name("main")
if func:
    try:
        lines = db.functions.get_pseudocode(func)
        print("\n".join(lines))
    except RuntimeError as e:
        print(f"Decompilation failed: {e}")
```

### Analyze Function Complexity

```python
complex_funcs = []
for func in db.functions:
    flowchart = db.functions.get_flowchart(func)
    if flowchart:
        block_count = len(flowchart)
        edge_count = sum(b.count_successors() for b in flowchart)
        cyclomatic = edge_count - block_count + 2

        if cyclomatic > 10:
            name = db.functions.get_name(func)
            complex_funcs.append((name, func.start_ea, cyclomatic))

complex_funcs.sort(key=lambda x: x[2], reverse=True)
print("Most complex functions:")
for name, addr, cc in complex_funcs[:10]:
    print(f"  {name}: complexity={cc} at 0x{addr:08X}")
```

### Search Byte Patterns

```python
# Search for NOP sled
pattern = b"\x90\x90\x90\x90"
results = db.bytes.find_binary_sequence(pattern)
for addr in results:
    print(f"Found NOP sled at 0x{addr:08X}")

# Search for x64 function prologue
prologue = b"\x55\x48\x89\xE5"  # push rbp; mov rbp, rsp
for addr in db.bytes.find_binary_sequence(prologue):
    print(f"Prologue at 0x{addr:08X}")
```

### Export to JSON

```python
import json

functions = []
for func in db.functions:
    name = db.functions.get_name(func)
    functions.append({
        "name": name,
        "start": f"0x{func.start_ea:08X}",
        "end": f"0x{func.end_ea:08X}",
        "size": func.end_ea - func.start_ea,
    })

output = {"module": db.module, "functions": functions}
print(json.dumps(output, indent=2))
```

## Tips

- **Error handling** - Always use try-except for decompilation and string operations
- **Check for None** - Functions like `get_function_by_name()` return None if not found
- **Use db.functions methods** - Call methods on `db.functions`, not on the func object:
  ```python
  # Wrong: func.get_callers()
  # Right: db.functions.get_callers(func)
  ```
- **String iteration** - Handle encoding errors gracefully:
  ```python
  for s in db.strings:
      try:
          content = str(s)
      except:
          continue  # Skip problematic strings
  ```

## Troubleshooting

**AttributeError: 'Xrefs' has no attribute 'get_xrefs_to':**
Use `db.xrefs.to_ea(addr)` not `db.xrefs.get_xrefs_to(addr)`

**AttributeError on func_t object:**
Call methods on `db.functions`, not on the func object.

**UnicodeDecodeError when reading strings:**
Wrap string access in try-except blocks.

**When encountering API errors:**
Check the API_REFERENCE.md for the correct method signatures.
