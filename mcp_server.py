from mcp.server.fastmcp import FastMCP, Context
from mcp.server.fastmcp.prompts import base
from pydantic import Field
import asyncio
import os
from pathlib import Path


mcp = FastMCP("DocumentMCP", log_level="ERROR")

# Session state: paths and directories the user has approved for the entire session
allowed_paths: set[Path] = set()
allowed_dirs: set[Path] = set()


async def get_docs_dir(ctx: Context | None = None) -> Path:
    if ctx is None:
        ctx = mcp.get_context()
    try:
        roots_result = await ctx.session.list_roots()
        if roots_result and roots_result.roots:
            return Path(roots_result.roots[0].uri.path)
    except Exception:
        pass
    local_path = Path("mcp_documents")
    if local_path.exists() and local_path.is_dir():
        return local_path.resolve()
    return Path(os.path.expanduser("~/mcp_documents"))


async def check_access(path: Path, ctx: Context) -> Path:
    """Check if a path is accessible. Raises ValueError if outside roots."""
    resolved = Path(os.path.expanduser(str(path))).resolve()

    # Already explicitly allowed
    if resolved in allowed_paths:
        return resolved

    # Check if under any allowed directory
    for allowed_dir in allowed_dirs:
        try:
            resolved.relative_to(allowed_dir)
            return resolved
        except ValueError:
            continue

    # Check if under any root
    try:
        roots_result = await ctx.session.list_roots()
        if roots_result and roots_result.roots:
            for root in roots_result.roots:
                root_path = Path(root.uri.path).resolve()
                try:
                    resolved.relative_to(root_path)
                    return resolved
                except ValueError:
                    continue
    except Exception:
        pass

    # Check default local path
    local_path = Path("mcp_documents").resolve()
    try:
        resolved.relative_to(local_path)
        return resolved
    except ValueError:
        pass

    # Outside all roots — raise error with the path info for client to handle
    raise PermissionError(f"ACCESS_REQUIRED:{resolved}")


def get_doc_path(doc_id: str, docs_dir: Path) -> Path:
    expanded = os.path.expanduser(doc_id)
    if os.path.isabs(expanded) or expanded.startswith("~"):
        return Path(expanded)
    # For bare filenames, check default docs dir first
    default = docs_dir / doc_id
    if default.exists():
        return default
    # Search allowed directories
    for d in allowed_dirs:
        candidate = d / doc_id
        if candidate.exists():
            return candidate
    # Return default even if not found (existence check happens later)
    return default


def read_doc_file(doc_id: str, docs_dir: Path) -> str:
    path = get_doc_path(doc_id, docs_dir)
    if not path.exists():
        raise ValueError(f"Doc {doc_id} not found")
    return path.read_text()


def write_doc_file(doc_id: str, content: str, docs_dir: Path):
    path = get_doc_path(doc_id, docs_dir)
    path.write_text(content)


# Tool to read a doc
@mcp.tool(
  name="read_doc_contents",
  description="Can be used to read and return the contents of a doc as a string"
)
async def read_document(
  doc_id : str = Field(description = "ID of the document we want to read"), 
  ctx : Context = Field(exclude=True),
):
  docs_dir = await get_docs_dir(ctx)
  path = get_doc_path(doc_id, docs_dir)
  if not path.exists():
      raise ValueError(f"Doc {doc_id} not found")
  path = await check_access(path, ctx)

  await ctx.info(f"Reading document: {doc_id}...")
  await ctx.report_progress(50, 100)

  content = path.read_text()

  await ctx.info("Done.")
  await ctx.report_progress(100, 100)
  return content


# Tool to create a doc
@mcp.tool(
  name="create_doc",
  description="Create a new document with the given content"
)
async def create_document(
  doc_id : str = Field(description = "Filename for the new document (e.g. notes.txt)"),
  content : str = Field(description = "Content of the document"),
  ctx : Context = Field(exclude=True),
):
  docs_dir = await get_docs_dir(ctx)
  path = get_doc_path(doc_id, docs_dir)
  if path.exists():
      raise ValueError(f"Doc {doc_id} already exists")
  path = await check_access(path, ctx)

  await ctx.info(f"Creating document: {doc_id}...")

  path.write_text(content)

  await ctx.info("Created.")
  return f"Created {doc_id}"


# Tool to edit a doc
@mcp.tool(
  name="edit_doc_contents",
  description="Can be used to edit the contents of a doc"
)
async def edit_document(
  doc_id : str = Field(description = "ID of the document we want to edit"),
  old_str : str = Field(description = "Content to replace. Must match exactly, including whitespaces"),
  new_str : str = Field(description = "Content that's to be replacing the old_str."),
  ctx : Context = Field(exclude=True),
):
  docs_dir = await get_docs_dir(ctx)
  path = get_doc_path(doc_id, docs_dir)
  if not path.exists():
      raise ValueError(f"Doc {doc_id} not found")
  path = await check_access(path, ctx)

  await ctx.info(f"Editing document: {doc_id}...")

  content = path.read_text()

  if old_str not in content:
      raise ValueError(f"Content not found in {doc_id}")

  new_content = content.replace(old_str, new_str)
  path.write_text(new_content)

  await ctx.info("Edited.")
  return f"Updated {doc_id}"


# Tool to delete a doc
@mcp.tool(
  name="delete_doc",
  description="Delete a document"
)
async def delete_document(
  doc_id : str = Field(description = "ID of the document to delete"),
  ctx : Context = Field(exclude=True),
):
  docs_dir = await get_docs_dir(ctx)
  path = get_doc_path(doc_id, docs_dir)
  if not path.exists():
      raise ValueError(f"Doc {doc_id} not found")
  path = await check_access(path, ctx)

  await ctx.info(f"Deleting document: {doc_id}...")
  path.unlink()

  await ctx.info("Deleted.")
  return f"Deleted {doc_id}"


@mcp.tool(
  name="allow_path",
  description="Grant the MCP server access to an absolute file path for this session"
)
async def allow_path(
  path: str = Field(description="Absolute path to allow (e.g. /Users/me/Desktop/file.txt)"),
):
  resolved = Path(os.path.expanduser(path)).resolve()
  allowed_paths.add(resolved)
  # Also allow the parent directory so other files in the same folder are accessible
  allowed_dirs.add(resolved.parent)
  return f"Access granted for {resolved} (and directory {resolved.parent})"


# Resource to return all files
@mcp.resource(
  "docs://list",
  mime_type="application/json"
)
async def list_files() -> list[str]:
  docs_dir = await get_docs_dir()
  files = []
  # Default docs directory
  if docs_dir.exists():
      files.extend(f.name for f in docs_dir.iterdir() if f.is_file())
  # Additional allowed directories
  for d in allowed_dirs:
      if d.exists() and d != docs_dir:
          files.extend(f.name for f in d.iterdir() if f.is_file())
  return files


# Resource to return recently modified files
@mcp.resource(
  "docs://recent",
  mime_type="application/json"
)
async def list_recent() -> list[str]:
  docs_dir = await get_docs_dir()
  files = []
  # Default docs directory
  if docs_dir.exists():
      files.extend(f for f in docs_dir.iterdir() if f.is_file())
  # Additional allowed directories
  for d in allowed_dirs:
      if d.exists() and d != docs_dir:
          files.extend(f for f in d.iterdir() if f.is_file())
  files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
  return [f.name for f in files[:5]]


# Resource to view file content as text
@mcp.resource(
  "docs://file/{filename}",
  mime_type="text/plain"
)
async def get_file_content(filename: str, ctx: Context) -> str:
  docs_dir = await get_docs_dir(ctx)
  path = get_doc_path(filename, docs_dir)
  if not path.exists():
      raise ValueError(f"Doc {filename} not found")
  path = await check_access(path, ctx)
  return path.read_text()


# Prompt to rewrite a doc in markdown format
@mcp.prompt(
  name="format",
  description="Rewrites the content of the document in markdown format"
)
def format_document(doc_id: str = Field(description="ID of the doc to format")) -> list[base.Message]:
  prompt = f"""
    Your goal is to reformat a document to be written with markdown syntax.

    The id of the document you need to reformat is:
    <document_id>
    {doc_id}
    </document_id>

    Use the 'read_doc_contents' tool to read the document first, then reformat
    the entire content using proper markdown syntax. Return the formatted content
    directly in your response. Do not save or modify the original document.
  """
  return [base.UserMessage(prompt)]
  
# Prompt to summarize a doc
@mcp.prompt(
  name="summarize",
  description="Summarized the content of the document"
)
def summarize_document(doc_id: str = Field(description="ID of the doc to summarize")) -> list[base.Message]:
  prompt = f"""
    Your goal is to summarize a document.

    The id of the document you need to summarize is:
    <document_id>
    {doc_id}
    </document_id>

    Use the 'read_doc_contents' tool to read the document first, then provide a
    concise summary of its contents. Return the summary directly in your response.
    Do not save or modify the original document.
  """
  return [base.UserMessage(prompt)]

# Prompt to rewrite a doc in a different tone/style
@mcp.prompt(
  name="rewrite",
  description="Rewrite a document in a different tone or style"
)
def rewrite_document(
  doc_id: str = Field(description="ID of the doc to rewrite"),
  tone: str = Field(description="Target tone: formal, casual, concise, technical, simple, persuasive, etc.")
) -> list[base.Message]:
  prompt = f"""
    Your goal is to rewrite a document in a different tone or style.

    The id of the document you need to rewrite is:
    <document_id>
    {doc_id}
    </document_id>

    The target tone/style is:
    <tone>
    {tone}
    </tone>

    Use the 'read_doc_contents' tool to read the document first, then rewrite
    the entire content using the specified tone. Preserve all factual information
    but adjust vocabulary, sentence structure, and voice to match the target tone.
    Return the rewritten content directly in your response. Do not save or modify
    the original document.
  """
  return [base.UserMessage(prompt)]


# Prompt to convert a doc between formats
@mcp.prompt(
  name="convert",
  description="Convert a document from one format to another (e.g. csv to json, yaml to markdown)"
)
def convert_document(
  doc_id: str = Field(description="ID of the doc to convert"),
  target_format: str = Field(description="Target format: json, csv, markdown, yaml, xml, html, plain text, etc.")
) -> list[base.Message]:
  prompt = f"""
    Your goal is to convert a document from its current format to a target format.

    The id of the document you need to convert is:
    <document_id>
    {doc_id}
    </document_id>

    The target format is:
    <target_format>
    {target_format}
    </target_format>

    Use the 'read_doc_contents' tool to read the document first, then convert
    the content to the target format. Preserve all data and structure as faithfully
    as possible. Return the converted content directly in your response. Do not
    save or modify the original document.
  """
  return [base.UserMessage(prompt)]


if __name__ == "__main__":
    mcp.run(transport="stdio")
