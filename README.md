# File System MCP

A CLI chat app that uses the Model Context Protocol (MCP) to let an LLM manage your filesystem. Ask questions, reference documents with `@`, and run commands like `/summarize` — all from your terminal.

## How It Works

The app starts an MCP server as a subprocess and connects to it over stdio. When you send a message, it's forwarded to OpenAI with the MCP tools (read, create, edit, delete documents) available as function calls. The LLM decides which tools to invoke, and the results are streamed back to you.

```
You → CLI → OpenAI (with MCP tools) → MCP Server → Filesystem
```

## Prerequisites

- Python 3.10+
- An [OpenAI API key](https://platform.openai.com/api-keys)

## Setup

### 1. Configure environment variables

Copy or edit `.env` in the project root:

```
OPENAI_API_KEY="sk-..."
OPENAI_MODEL="gpt-4o"
USE_UV=1
```

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI API key (required) |
| `OPENAI_MODEL` | Model to use, e.g. `gpt-4o` (required) |
| `USE_UV` | Set to `1` to run the MCP server via `uv`, `0` for plain `python` |

### 2. Install dependencies

**With uv (recommended):**

```bash
pip install uv
uv venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e .
```

**Without uv:**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 3. Run

```bash
python main.py
```

Or with uv:

```bash
uv run main.py
```

## Usage

### Chatting

Type a message and press Enter:

```
> What files do I have?
```

### Referencing documents

Use `@filename` to include a document's content in your query. Filenames with spaces work too:

```
> Summarize @pokemon.md
> What's in @Family Tree.pdf
```

Tab completion activates after typing `@`. The dropdown includes files from the default `mcp_documents/` directory and any directories you've granted access to.

### Commands

Type `/` to see available commands. Commands take a document ID as the first argument:

| Command | Syntax | Description |
|---|---|---|
| `/summarize` | `/summarize <doc>` | Summarize a document |
| `/format` | `/format <doc>` | Reformat a document to markdown |
| `/rewrite` | `/rewrite <doc> <tone>` | Rewrite in a different tone (formal, casual, concise, technical, persuasive, simple) |
| `/convert` | `/convert <doc> <format>` | Convert to another format (json, csv, markdown, yaml, xml, html, plain text) |

```
> /summarize pokemon.md
> /format pokemon.md
> /rewrite pokemon.md formal
> /convert pokemon.csv json
```

Tab completion works for commands, document names, and tone/format values.

### Custom root directories

Point the server at specific directories with `--roots`:

```bash
python main.py --roots ~/my_docs ./data
```

### Additional MCP servers

Load extra MCP server scripts as positional arguments:

```bash
python main.py my_server.py another_server.py
```

### File access permissions

When you reference a file outside the allowed directories (`--roots` or default `mcp_documents/`), a permission prompt appears:

```
The MCP server wants to access:
/Users/prem/Desktop/secret.txt
This path is outside the allowed directories.
Allow access? (1=Allow Once, 2=Always Allow, 3=Deny): 
```

- **Allow Once (1)** — grants access for this single operation
- **Always Allow (2)** — grants access for the rest of the session, and adds the parent directory to the allowed list so other files in the same folder are accessible via `@` without further prompts
- **Deny (3)** — blocks access

After "Always Allow", the `@` tab-completion dropdown refreshes to include files from the newly allowed directory.

Files are checked for existence before the permission prompt, so you won't be prompted for files that don't exist.

## MCP Server

The built-in server (`mcp_server.py`) exposes:

### Tools

| Tool | Description |
|---|---|
| `read_doc_contents` | Read a document's contents |
| `create_doc` | Create a new document |
| `edit_doc_contents` | Replace text within a document |
| `delete_doc` | Delete a document |
| `allow_path` | Grant access to a file path (called by the client after permission is granted) |

### Resources

| URI | Returns |
|---|---|
| `docs://list` | All filenames in the documents directory and any allowed directories |
| `docs://recent` | 5 most recently modified files |
| `docs://file/{filename}` | Contents of a specific file |

### Prompts

| Prompt | Syntax | Description |
|---|---|---|
| `/summarize` | `/summarize <doc>` | Summarize a document and return the result in chat |
| `/format` | `/format <doc>` | Reformat a document to markdown and return the result in chat |
| `/rewrite` | `/rewrite <doc> <tone>` | Rewrite in a different tone and return the result in chat |
| `/convert` | `/convert <doc> <format>` | Convert between formats and return the result in chat |

## Project Structure

```
├── main.py              # Entry point — parses args, starts MCP clients and CLI
├── mcp_client.py        # MCP client wrapper (connects to servers via stdio)
├── mcp_server.py        # MCP server with tools, resources, prompts, and permission checks
├── core/
│   ├── cli.py           # Terminal UI (prompt-toolkit, tab completion, key bindings)
│   ├── cli_chat.py      # CLI chat logic (@mentions, /commands, permission handling)
│   ├── chat.py          # Base chat loop (LLM ↔ tool execution cycle)
│   ├── llm.py           # OpenAI API wrapper
│   └── tools.py         # Converts MCP tools to OpenAI function-calling format
├── mcp_documents/       # Sample documents directory
├── pyproject.toml       # Project metadata and dependencies
└── .env                 # API keys and config
```

## Adding documents

Place files in `mcp_documents/` (or a custom `--roots` directory). The server reads from there by default. Supported formats are any text-based file.

To add documents programmatically, use the `create_doc` tool through the chat interface.
