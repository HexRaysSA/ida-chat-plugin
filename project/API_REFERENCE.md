# IDA Domain Quick Reference

Quick reference for the IDA Domain API

## Table of Contents

- [Database](#database)
- [Functions](#functions)
- [Instructions](#instructions)
- [Segments](#segments)
- [Heads](#heads)
- [Bytes](#bytes)
- [Strings](#strings)
- [Names](#names)
- [Comments](#comments)
- [Entries](#entries)
- [Xrefs](#xrefs)
- [Types](#types)
- [Signature Files](#signature-files)
- [Operands](#operands)
- [FlowChart](#flowchart)
- [Enums Reference](#enums-reference)

---

## Database

The `Database` class is the main entry point for all operations.

### Opening a Database

```python
from ida_domain import Database

# Library mode: Open a binary file
with Database.open("binary.exe", save_on_close=True) as db:
    print(f"Module: {db.module}")

# IDA GUI mode: Get current database
db = Database.open()
```

### Database Properties

| Property | Type | Description |
|----------|------|-------------|
| `db.path` | `str` | Input file path |
| `db.module` | `str` | Module name |
| `db.base_address` | `ea_t` | Image base address |
| `db.minimum_ea` | `ea_t` | Minimum effective address |
| `db.maximum_ea` | `ea_t` | Maximum effective address |
| `db.architecture` | `str` | Processor architecture (e.g., "metapc") |
| `db.bitness` | `int` | Application bitness (32 or 64) |
| `db.format` | `str` | File format type |
| `db.md5` | `str` | MD5 hash of input file |
| `db.sha256` | `str` | SHA256 hash of input file |
| `db.crc32` | `int` | CRC32 checksum |
| `db.filesize` | `int` | Input file size |
| `db.load_time` | `str` | Database load timestamp |
| `db.execution_mode` | `ExecutionMode` | User or Kernel mode |
| `db.compiler_information` | `CompilerInformation` | Compiler details |
| `db.current_ea` | `ea_t` | Current screen address (read/write) |
| `db.start_ip` | `ea_t` | Start instruction pointer |

### Database Methods

```python
# Check if address is valid
db.is_valid_ea(0x401000)  # True/False

# Execute a script file
db.execute_script("my_script.py")
```

### Entity Handlers

Access entity handlers via properties:

```python
db.functions      # Functions handler
db.instructions   # Instructions handler
db.segments       # Segments handler
db.heads          # Heads handler
db.bytes          # Bytes handler
db.strings        # Strings handler
db.names          # Names handler
db.comments       # Comments handler
db.entries        # Entries handler
db.xrefs          # Cross-references handler
db.types          # Types handler
db.signature_files # FLIRT signatures handler
```

---

## Functions

### Iterating Functions

```python
# All functions
for func in db.functions:
    name = db.functions.get_name(func)
    print(f"{name}: 0x{func.start_ea:08X}-0x{func.end_ea:08X}")

# Function count
print(f"Total: {len(db.functions)} functions")

# Functions in address range
for func in db.functions.get_between(0x401000, 0x402000):
    print(db.functions.get_name(func))
```

### Finding Functions

```python
# By address (containing)
func = db.functions.get_at(0x401234)

# By name
func = db.functions.get_function_by_name("main")

# Next function after address
func = db.functions.get_next(0x401000)
```

### Function Properties

```python
func = db.functions.get_at(0x401000)

# Name and signature
name = db.functions.get_name(func)
sig = db.functions.get_signature(func)  # e.g., "int __cdecl(int, char **)"

# Disassembly and pseudocode
disasm = db.functions.get_disassembly(func)  # List[str]
pseudo = db.functions.get_pseudocode(func)   # List[str]
micro = db.functions.get_microcode(func)     # List[str]

# Control flow
flowchart = db.functions.get_flowchart(func)

# Instructions
for insn in db.functions.get_instructions(func):
    print(f"0x{insn.ea:08X}: {db.instructions.get_mnemonic(insn)}")

# Call graph
callers = db.functions.get_callers(func)  # Functions that call this
callees = db.functions.get_callees(func)  # Functions this calls

# Flags
flags = db.functions.get_flags(func)
if FunctionFlags.THUNK in flags:
    print("This is a thunk function")
```

### Function Manipulation

```python
# Create/remove functions
db.functions.create(0x401000)
db.functions.remove(0x401000)

# Rename
db.functions.set_name(func, "my_function")

# Comments
db.functions.set_comment(func, "Main entry point")
comment = db.functions.get_comment(func)
```

### Local Variables

```python
# Get all local variables
for lvar in db.functions.get_local_variables(func):
    print(f"{lvar.name}: {lvar.type_str} (arg={lvar.is_argument})")

# Find by name
lvar = db.functions.get_local_variable_by_name(func, "argc")

# Get references to a variable
refs = db.functions.get_local_variable_references(func, lvar)
for ref in refs:
    print(f"Line {ref.line_number}: {ref.access_type.name} in {ref.context}")
```

---

## Instructions

### Iterating Instructions

```python
# All instructions in database
for insn in db.instructions:
    mnem = db.instructions.get_mnemonic(insn)
    print(f"0x{insn.ea:08X}: {mnem}")

# Instructions in range
for insn in db.instructions.get_between(0x401000, 0x401100):
    print(db.instructions.get_disassembly(insn))
```

### Decoding Instructions

```python
# Decode at address
insn = db.instructions.get_at(0x401000)

# Previous instruction
prev = db.instructions.get_previous(0x401005)

# Check validity
if db.instructions.is_valid(insn):
    print("Valid instruction")
```

### Instruction Properties

```python
insn = db.instructions.get_at(0x401000)

# Mnemonic and disassembly
mnem = db.instructions.get_mnemonic(insn)      # e.g., "mov"
disasm = db.instructions.get_disassembly(insn) # e.g., "mov eax, ebx"

# Instruction type checks
if db.instructions.is_call_instruction(insn):
    print("This is a call")
if db.instructions.is_indirect_jump_or_call(insn):
    print("Indirect control flow")
if db.instructions.breaks_sequential_flow(insn):
    print("Flow stops here (ret, jmp, etc.)")
```

### Operands

```python
# Get operand count
count = db.instructions.get_operands_count(insn)

# Get specific operand
op0 = db.instructions.get_operand(insn, 0)

# Get all operands
for op in db.instructions.get_operands(insn):
    info = op.get_info()
    print(f"  Op{info.number}: {info.type.name}, access={info.access_type.name}")
```

---

## Segments

### Iterating Segments

```python
# All segments
for seg in db.segments:
    name = db.segments.get_name(seg)
    size = db.segments.get_size(seg)
    print(f"{name}: 0x{seg.start_ea:08X}-0x{seg.end_ea:08X} ({size} bytes)")

# Segment count
print(f"Total: {len(db.segments)} segments")
```

### Finding Segments

```python
# By address
seg = db.segments.get_at(0x401000)

# By name
seg = db.segments.get_by_name(".text")
```

### Segment Properties

```python
seg = db.segments.get_at(0x401000)

name = db.segments.get_name(seg)
size = db.segments.get_size(seg)
bitness = db.segments.get_bitness(seg)    # 16, 32, or 64
seg_class = db.segments.get_class(seg)    # e.g., "CODE"
comment = db.segments.get_comment(seg)
```

### Segment Manipulation

```python
from ida_domain.segments import AddSegmentFlags, SegmentPermissions, AddressingMode

# Add new segment
new_seg = db.segments.add(
    seg_para=0,
    start_ea=0x500000,
    end_ea=0x501000,
    seg_name=".myseg",
    seg_class="DATA"
)

# Append segment after last
new_seg = db.segments.append(
    seg_para=0,
    seg_size=0x1000,
    seg_name=".extra"
)

# Rename
db.segments.set_name(seg, "new_name")

# Permissions
db.segments.set_permissions(seg, SegmentPermissions.READ | SegmentPermissions.EXEC)
db.segments.add_permissions(seg, SegmentPermissions.WRITE)
db.segments.remove_permissions(seg, SegmentPermissions.WRITE)

# Addressing mode
db.segments.set_addressing_mode(seg, AddressingMode.BIT64)

# Comments
db.segments.set_comment(seg, "Code section")
```

---

## Heads

Heads are the starting addresses of items (instructions or data).

### Iterating Heads

```python
# All heads
for ea in db.heads:
    if db.heads.is_code(ea):
        print(f"0x{ea:08X}: code")
    elif db.heads.is_data(ea):
        print(f"0x{ea:08X}: data")

# Heads in range
for ea in db.heads.get_between(0x401000, 0x402000):
    print(f"0x{ea:08X}")
```

### Navigation

```python
# Next/previous head
next_ea = db.heads.get_next(0x401000)
prev_ea = db.heads.get_previous(0x401010)
```

### Head Properties

```python
ea = 0x401000

# Type checks
db.heads.is_head(ea)     # Is start of an item
db.heads.is_tail(ea)     # Is part of item but not start
db.heads.is_code(ea)     # Is code
db.heads.is_data(ea)     # Is data
db.heads.is_unknown(ea)  # Is unknown/undefined

# Size and bounds
size = db.heads.size(ea)           # Size in bytes
start, end = db.heads.bounds(ea)   # Item bounds
```

---

## Bytes

### Reading Data

```python
# Single values
byte_val = db.bytes.get_byte_at(0x401000)
word_val = db.bytes.get_word_at(0x401000)
dword_val = db.bytes.get_dword_at(0x401000)
qword_val = db.bytes.get_qword_at(0x401000)

# Floating point
float_val = db.bytes.get_float_at(0x401000)
double_val = db.bytes.get_double_at(0x401000)

# Bulk read
data = db.bytes.get_bytes_at(0x401000, 100)  # 100 bytes

# Strings
string = db.bytes.get_string_at(0x401000)
cstring = db.bytes.get_cstring_at(0x401000)

# Disassembly text
disasm = db.bytes.get_disassembly_at(0x401000)
```

### Writing Data

```python
# Set values (modify database view)
db.bytes.set_byte_at(0x401000, 0x90)
db.bytes.set_word_at(0x401000, 0x9090)
db.bytes.set_dword_at(0x401000, 0x90909090)
db.bytes.set_qword_at(0x401000, 0x9090909090909090)
db.bytes.set_bytes_at(0x401000, b"\x90\x90\x90")
```

### Patching (Preserves Original)

```python
# Patch (saves original for revert)
db.bytes.patch_byte_at(0x401000, 0x90)
db.bytes.patch_word_at(0x401000, 0x9090)
db.bytes.patch_dword_at(0x401000, 0x90909090)
db.bytes.patch_qword_at(0x401000, 0x9090909090909090)
db.bytes.patch_bytes_at(0x401000, b"\x90\x90\x90")

# Revert to original
db.bytes.revert_byte_at(0x401000)

# Get original values
orig = db.bytes.get_original_byte_at(0x401000)
orig_bytes = db.bytes.get_original_bytes_at(0x401000, 10)
```

### Searching

```python
from ida_domain.bytes import SearchFlags

# Find byte pattern
ea = db.bytes.find_bytes_between(b"\x55\x8B\xEC")

# Find text
ea = db.bytes.find_text_between("error", flags=SearchFlags.CASE)

# Find immediate value in instructions
ea = db.bytes.find_immediate_between(0x12345678)

# Find all occurrences
all_matches = db.bytes.find_binary_sequence(b"\x90\x90")
```

### Data Type Checks

```python
ea = 0x401000

db.bytes.is_code_at(ea)
db.bytes.is_data_at(ea)
db.bytes.is_unknown_at(ea)
db.bytes.is_head_at(ea)
db.bytes.is_tail_at(ea)

# Specific data types
db.bytes.is_byte_at(ea)
db.bytes.is_word_at(ea)
db.bytes.is_dword_at(ea)
db.bytes.is_qword_at(ea)
db.bytes.is_float_at(ea)
db.bytes.is_string_literal_at(ea)
db.bytes.is_struct_at(ea)
```

### Creating Data Items

```python
from ida_domain.strings import StringType

# Create data types
db.bytes.create_byte_at(0x401000)
db.bytes.create_word_at(0x401000)
db.bytes.create_dword_at(0x401000)
db.bytes.create_qword_at(0x401000)
db.bytes.create_float_at(0x401000)
db.bytes.create_double_at(0x401000)

# Create arrays
db.bytes.create_dword_at(0x401000, count=10)

# Create string
db.bytes.create_string_at(0x401000, string_type=StringType.C)

# Create struct
db.bytes.create_struct_at(0x401000, count=1, tid=struct_type_id)
```

### Navigation

```python
# Next/previous head
next_head = db.bytes.get_next_head(0x401000)
prev_head = db.bytes.get_previous_head(0x401010)

# Next/previous address
next_addr = db.bytes.get_next_address(0x401000)
prev_addr = db.bytes.get_previous_address(0x401010)

# Data item size
size = db.bytes.get_data_size_at(0x401000)
```

---

## Strings

### Iterating Strings

```python
# All strings
for s in db.strings:
    print(f"0x{s.address:08X}: {s}")

# String count
print(f"Total: {len(db.strings)} strings")

# By index
first_string = db.strings[0]

# Strings in range
for s in db.strings.get_between(0x401000, 0x500000):
    print(f"0x{s.address:08X}: {s}")
```

### String Properties

```python
s = db.strings.get_at(0x401000)
if s:
    print(f"Address: 0x{s.address:08X}")
    print(f"Length: {s.length}")
    print(f"Type: {s.type.name}")        # C, PASCAL, etc.
    print(f"Encoding: {s.encoding}")     # e.g., "utf-8"
    print(f"Contents: {str(s)}")         # Decoded string
    print(f"Raw bytes: {bytes(s)}")      # Raw UTF-8 bytes
```

### Rebuilding String List

```python
from ida_domain.strings import StringListConfig, StringType

# Rebuild with custom config
config = StringListConfig(
    string_types=[StringType.C, StringType.C_16],
    min_len=4,
    only_ascii_7bit=False
)
db.strings.rebuild(config)

# Clear string list
db.strings.clear()
```

---

## Names

### Iterating Names

```python
# All named locations
for ea, name in db.names:
    print(f"0x{ea:08X}: {name}")

# Name count
print(f"Total: {len(db.names)} names")

# By index
ea, name = db.names[0]
```

### Getting Names

```python
# Name at address
name = db.names.get_at(0x401000)

# Demangled name
demangled = db.names.get_demangled_name(0x401000)

# Demangle any name
clean_name = db.names.demangle_name("_Z3fooi")
```

### Setting Names

```python
from ida_domain.names import SetNameFlags

# Set name
db.names.set_name(0x401000, "my_function")

# Set with flags
db.names.set_name(0x401000, "my_func", SetNameFlags.PUBLIC)

# Force set (tries variations if exists)
db.names.force_name(0x401000, "duplicate_name")

# Delete name
db.names.delete(0x401000)
```

### Name Properties

```python
# Validation
db.names.is_valid_name("my_function")  # True/False

# Public/weak status
db.names.is_public_name(0x401000)
db.names.make_name_public(0x401000)
db.names.make_name_non_public(0x401000)

db.names.is_weak_name(0x401000)
db.names.make_name_weak(0x401000)
db.names.make_name_non_weak(0x401000)
```

---

## Comments

### Getting Comments

```python
from ida_domain.comments import CommentKind, ExtraCommentKind

# Regular comment at address
info = db.comments.get_at(0x401000)
if info:
    print(f"Comment: {info.comment}")

# Repeatable comment
info = db.comments.get_at(0x401000, CommentKind.REPEATABLE)

# Extra comments (anterior/posterior)
anterior = db.comments.get_extra_at(0x401000, 0, ExtraCommentKind.ANTERIOR)
posterior = db.comments.get_extra_at(0x401000, 0, ExtraCommentKind.POSTERIOR)

# All extra comments
for comment in db.comments.get_all_extra_at(0x401000, ExtraCommentKind.ANTERIOR):
    print(comment)
```

### Setting Comments

```python
# Set regular comment
db.comments.set_at(0x401000, "This is a comment")

# Set repeatable comment
db.comments.set_at(0x401000, "Repeatable", CommentKind.REPEATABLE)

# Set extra comments
db.comments.set_extra_at(0x401000, 0, "Before line", ExtraCommentKind.ANTERIOR)
db.comments.set_extra_at(0x401000, 0, "After line", ExtraCommentKind.POSTERIOR)
```

### Deleting Comments

```python
db.comments.delete_at(0x401000)
db.comments.delete_at(0x401000, CommentKind.REPEATABLE)
db.comments.delete_extra_at(0x401000, 0, ExtraCommentKind.ANTERIOR)
```

### Iterating Comments

```python
# All comments
for info in db.comments:
    print(f"0x{info.ea:08X}: {info.comment}")

# Filter by kind
for info in db.comments.get_all(CommentKind.REPEATABLE):
    print(f"0x{info.ea:08X}: {info.comment}")
```

---

## Entries

Entry points include exported functions and program entry.

### Iterating Entries

```python
# All entries
for entry in db.entries:
    print(f"0x{entry.address:08X}: {entry.name} (ord={entry.ordinal})")

# Entry count
print(f"Total: {len(db.entries)} entries")

# By index
entry = db.entries[0]
```

### Finding Entries

```python
# By ordinal
entry = db.entries.get_by_ordinal(1)

# By address
entry = db.entries.get_at(0x401000)

# By name
entry = db.entries.get_by_name("main")

# Check existence
if db.entries.exists(ordinal=1):
    print("Entry exists")
```

### Entry Properties

```python
entry = db.entries.get_at(0x401000)
if entry:
    print(f"Address: 0x{entry.address:08X}")
    print(f"Name: {entry.name}")
    print(f"Ordinal: {entry.ordinal}")
    if entry.has_forwarder():
        print(f"Forwarder: {entry.forwarder_name}")
```

### Entry Manipulation

```python
# Add entry
db.entries.add(0x401000, "my_export", ordinal=100)

# Rename
db.entries.rename(100, "new_name")

# Set forwarder
db.entries.set_forwarder(100, "other_dll.function")

# Get all forwarders
for fwd in db.entries.get_forwarders():
    print(f"Ordinal {fwd.ordinal}: {fwd.name}")
```

### Iterators

```python
# All ordinals
for ordinal in db.entries.get_ordinals():
    print(ordinal)

# All addresses
for ea in db.entries.get_addresses():
    print(f"0x{ea:08X}")

# All names
for name in db.entries.get_names():
    print(name)
```

---

## Xrefs

Cross-references show relationships between addresses.

### Getting Xrefs

```python
from ida_domain.xrefs import XrefsFlags

# All xrefs TO an address
for xref in db.xrefs.to_ea(0x401000):
    print(f"From 0x{xref.from_ea:08X}, type={xref.type.name}")

# All xrefs FROM an address
for xref in db.xrefs.from_ea(0x401000):
    print(f"To 0x{xref.to_ea:08X}, type={xref.type.name}")

# Filter by flags
for xref in db.xrefs.to_ea(0x401000, XrefsFlags.CODE_NOFLOW):
    print(f"Code xref from 0x{xref.from_ea:08X}")
```

### Convenience Methods

```python
# Code references (addresses only)
for ea in db.xrefs.code_refs_to_ea(0x401000):
    print(f"Code ref from 0x{ea:08X}")

for ea in db.xrefs.code_refs_from_ea(0x401000):
    print(f"Code ref to 0x{ea:08X}")

# Data references
for ea in db.xrefs.data_refs_to_ea(0x401000):
    print(f"Data ref from 0x{ea:08X}")

# Calls specifically
for ea in db.xrefs.calls_to_ea(0x401000):
    print(f"Called from 0x{ea:08X}")

for ea in db.xrefs.calls_from_ea(0x401000):
    print(f"Calls 0x{ea:08X}")

# Jumps
for ea in db.xrefs.jumps_to_ea(0x401000):
    print(f"Jump from 0x{ea:08X}")

# Data access
for ea in db.xrefs.reads_of_ea(0x402000):
    print(f"Read by 0x{ea:08X}")

for ea in db.xrefs.writes_to_ea(0x402000):
    print(f"Written by 0x{ea:08X}")
```

### Caller Information

```python
# Detailed caller info for a function
for caller in db.xrefs.get_callers(0x401000):
    print(f"Called from 0x{caller.ea:08X}")
    print(f"  Function: {caller.name}")
    print(f"  Type: {caller.xref_type.name}")
```

### Xref Properties

```python
xref = next(db.xrefs.to_ea(0x401000))

# Basic info
print(f"From: 0x{xref.from_ea:08X}")
print(f"To: 0x{xref.to_ea:08X}")
print(f"Type: {xref.type.name}")
print(f"Is code: {xref.is_code}")
print(f"User defined: {xref.user}")

# Type checks
xref.is_call()   # Is a call reference
xref.is_jump()   # Is a jump reference
xref.is_read()   # Is a data read
xref.is_write()  # Is a data write
xref.is_flow()   # Is ordinary flow
```

---

## Types

### Getting Types

```python
# By name
tinfo = db.types.get_by_name("DWORD")

# At address
tinfo = db.types.get_at(0x401000)

# Get type details
if tinfo:
    details = db.types.get_details(tinfo)
    print(f"Name: {details.name}")
    print(f"Size: {details.size}")
    print(f"Attributes: {details.attributes}")
```

### Iterating Types

```python
from ida_domain.types import TypeKind

# All named types
for tinfo in db.types.get_all():
    details = db.types.get_details(tinfo)
    print(f"{details.name}: {details.size} bytes")

# Numbered types
for tinfo in db.types.get_all(type_kind=TypeKind.NUMBERED):
    print(db.types.get_details(tinfo).name)
```

### Applying Types

```python
from ida_domain.types import TypeApplyFlags

tinfo = db.types.get_by_name("DWORD")
db.types.apply_at(tinfo, 0x402000, TypeApplyFlags.DEFINITE)
```

### Type Libraries

```python
from pathlib import Path

# Load library
lib = db.types.load_library(Path("mssdk.til"))

# Import types from library
db.types.import_from_library(lib)

# Import specific type
db.types.import_type(lib, "HANDLE")

# Create new library
new_lib = db.types.create_library(Path("my_types.til"), "My Types")

# Export types to library
db.types.export_to_library(new_lib)

# Save library
db.types.save_library(new_lib, Path("my_types.til"))

# Unload library
db.types.unload_library(lib)
```

### Parsing Type Declarations

```python
from ida_domain.types import TypeFormattingFlags

# Parse header file
errors = db.types.parse_header_file(lib, Path("my_header.h"))

# Parse declarations from string
errors = db.types.parse_declarations(lib, "typedef int MYINT;")

# Parse single declaration
tinfo = db.types.parse_one_declaration(lib, "int foo;", "foo")
```

### Type Comments

```python
db.types.set_comment(tinfo, "This is my type")
comment = db.types.get_comment(tinfo)
```

---

## Signature Files

FLIRT signature files for library function recognition.

### Applying Signatures

```python
from pathlib import Path

# Apply signature file
results = db.signature_files.apply(Path("vc32rtf.sig"))
for result in results:
    print(f"File: {result.path}")
    print(f"Matches: {result.matches}")
    for match in result.functions:
        print(f"  0x{match.addr:08X}: {match.name}")

# Probe only (don't save changes)
results = db.signature_files.apply(Path("vc32rtf.sig"), probe_only=True)
```

### Creating Signatures

```python
# Create .pat and .sig files from database
files = db.signature_files.create()
for path in files:
    print(f"Created: {path}")

# Create only .pat file
files = db.signature_files.create(pat_only=True)
```

### Listing Available Signatures

```python
# Get all available .sig files
for path in db.signature_files.get_files():
    print(path)

# From custom directories
sig_files = db.signature_files.get_files([Path("/custom/sigs")])
```

---

## Operands

Operands are accessed through instructions.

### Getting Operands

```python
insn = db.instructions.get_at(0x401000)

# All operands
for op in db.instructions.get_operands(insn):
    info = op.get_info()
    print(f"Op{info.number}: {info.type.name}")

# Specific operand
op0 = db.instructions.get_operand(insn, 0)
op1 = db.instructions.get_operand(insn, 1)
```

### Operand Information

```python
op = db.instructions.get_operand(insn, 0)
info = op.get_info()

print(f"Number: {info.number}")
print(f"Type: {info.type.name}")           # REGISTER, MEMORY, IMMEDIATE, etc.
print(f"Data type: {info.data_type.name}") # BYTE, WORD, DWORD, etc.
print(f"Access: {info.access_type.name}")  # READ, WRITE, READ_WRITE
print(f"Size: {info.size_bytes} bytes")
```

### Register Operands

```python
from ida_domain.operands import RegisterOperand

if isinstance(op, RegisterOperand):
    print(f"Register: {op.get_register_name()}")
    print(f"Register number: {op.register_number}")
```

### Immediate Operands

```python
from ida_domain.operands import ImmediateOperand

if isinstance(op, ImmediateOperand):
    print(f"Value: 0x{op.get_value():X}")
    if op.is_address():
        print(f"Name: {op.get_name()}")
```

### Memory Operands

```python
from ida_domain.operands import MemoryOperand

if isinstance(op, MemoryOperand):
    if op.is_direct_memory():
        print(f"Address: 0x{op.get_address():X}")
        print(f"Name: {op.get_name()}")
    elif op.is_register_based():
        print(f"Displacement: {op.get_displacement()}")
```

---

## FlowChart

Control flow graphs for functions.

### Creating FlowCharts

```python
from ida_domain.flowchart import FlowChartFlags

func = db.functions.get_at(0x401000)

# Basic flowchart
fc = db.functions.get_flowchart(func)

# With predecessor info
fc = db.functions.get_flowchart(func, FlowChartFlags.PREDS)
```

### Iterating Basic Blocks

```python
# All blocks
for block in fc:
    print(f"Block {block.id}: 0x{block.start_ea:08X}-0x{block.end_ea:08X}")

# Block count
print(f"Total: {len(fc)} basic blocks")

# By index
first_block = fc[0]
```

### Basic Block Navigation

```python
block = fc[0]

# Successors (blocks this leads to)
for succ in block.get_successors():
    print(f"  -> Block {succ.id}")

# Predecessors (blocks leading here)
for pred in block.get_predecessors():
    print(f"  <- Block {pred.id}")

# Counts
print(f"Successors: {block.count_successors()}")
print(f"Predecessors: {block.count_predecessors()}")
```

### Block Instructions

```python
for block in fc:
    print(f"Block {block.id}:")
    for insn in block.get_instructions():
        print(f"  0x{insn.ea:08X}: {db.instructions.get_mnemonic(insn)}")
```

---

## Enums Reference

### XrefType

```python
from ida_domain.xrefs import XrefType

# Data xref types
XrefType.OFFSET         # Offset reference
XrefType.WRITE          # Write access
XrefType.READ           # Read access
XrefType.TEXT           # Text reference
XrefType.INFORMATIONAL  # Informational
XrefType.SYMBOLIC       # Enum member reference

# Code xref types
XrefType.CALL_FAR       # Far call
XrefType.CALL_NEAR      # Near call
XrefType.JUMP_FAR       # Far jump
XrefType.JUMP_NEAR      # Near jump
XrefType.ORDINARY_FLOW  # Sequential flow
```

### StringType

```python
from ida_domain.strings import StringType

StringType.C            # Null-terminated
StringType.C_16         # 16-bit null-terminated
StringType.C_32         # 32-bit null-terminated
StringType.PASCAL       # Length-prefixed
StringType.PASCAL_16    # 16-bit length-prefixed
StringType.LEN2         # 2-byte length prefix
StringType.LEN4         # 4-byte length prefix
```

### OperandType

```python
from ida_domain.operands import OperandType

OperandType.VOID        # No operand
OperandType.REGISTER    # Register
OperandType.MEMORY      # Memory access
OperandType.PHRASE      # Base register + displacement
OperandType.DISPLACEMENT # Register + displacement
OperandType.IMMEDIATE   # Immediate value
OperandType.FAR_ADDRESS # Far address
OperandType.NEAR_ADDRESS # Near address
```

### AccessType

```python
from ida_domain.operands import AccessType

AccessType.NONE         # Not accessed
AccessType.READ         # Read only
AccessType.WRITE        # Write only
AccessType.READ_WRITE   # Both
```

### FunctionFlags

```python
from ida_domain.functions import FunctionFlags

FunctionFlags.NORET     # Doesn't return
FunctionFlags.FAR       # Far function
FunctionFlags.LIB       # Library function
FunctionFlags.THUNK     # Thunk/jump function
FunctionFlags.FRAME     # Uses frame pointer
FunctionFlags.HIDDEN    # Hidden chunk
FunctionFlags.TAIL      # Function tail
FunctionFlags.LUMINA    # Lumina info
```

### SegmentPermissions

```python
from ida_domain.segments import SegmentPermissions

SegmentPermissions.NONE   # No permissions
SegmentPermissions.READ   # Readable
SegmentPermissions.WRITE  # Writable
SegmentPermissions.EXEC   # Executable
SegmentPermissions.ALL    # All permissions
```

### AddressingMode

```python
from ida_domain.segments import AddressingMode

AddressingMode.BIT16    # 16-bit
AddressingMode.BIT32    # 32-bit
AddressingMode.BIT64    # 64-bit
```

### CommentKind

```python
from ida_domain.comments import CommentKind

CommentKind.REGULAR     # Regular comment
CommentKind.REPEATABLE  # Repeatable comment
CommentKind.ALL         # Both types
```

### ExtraCommentKind

```python
from ida_domain.comments import ExtraCommentKind

ExtraCommentKind.ANTERIOR   # Before line
ExtraCommentKind.POSTERIOR  # After line
```

### FlowChartFlags

```python
from ida_domain.flowchart import FlowChartFlags

FlowChartFlags.NONE     # Default
FlowChartFlags.NOEXT    # No external blocks
FlowChartFlags.PREDS    # Compute predecessors
```

### ExecutionMode

```python
from ida_domain.database import ExecutionMode

ExecutionMode.User      # User mode
ExecutionMode.Kernel    # Kernel mode
```
