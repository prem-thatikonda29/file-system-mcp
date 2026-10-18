from mcp.server.fastmcp import FastMCP, Context
from mcp.server.fastmcp.prompts import base
from pydantic import BaseModel, Field
import asyncio
import os
from pathlib import Path


mcp = FastMCP("DocumentMCP", log_level="ERROR")

# Session state: paths and directories the user has approved for the entire session.
# Only check_access() adds to these, and only after the user says yes via elicitation.
allowed_paths: set[Path] = set()
allowed_dirs: set[Path] = set()

ACCESS_SCOPES = ["once", "file", "folder"]


class AccessGrant(BaseModel):
    """Form the client shows the user when a path is outside the allowed directories."""
    scope: str = Field(
        default="once",
        title="Allow access",
        description="once = just this request, file = this file for the session, "
                    "folder = everything in this file's folder for the session",
        json_schema_extra={
            "enum": ACCESS_SCOPES,
            "enumNames": ["Allow once", "Always allow this file", "Always allow this folder"],
        },
    )


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


async def is_allowed(resolved: Path, ctx: Context) -> bool:
    """True if a resolved path is under a root, the default docs dir, or a session grant."""
    if resolved in allowed_paths:
        return True

    allowed_bases = set(allowed_dirs)
    allowed_bases.add(Path("mcp_documents").resolve())
    try:
        roots_result = await ctx.session.list_roots()
        if roots_result and roots_result.roots:
            allowed_bases.update(Path(root.uri.path).resolve() for root in roots_result.roots)
    except Exception:
        pass

    return any(resolved.is_relative_to(base) for base in allowed_bases)


async def check_access(path: Path, ctx: Context) -> Path:
    """Return the resolved path if accessible, asking the user when it's outside the roots.

    Call this before checking whether the file exists, so callers can't use the
    "not found" vs "denied" difference to probe paths outside the roots.
    Raises PermissionError if the user declines or the client can't ask them.
    """
    resolved = Path(os.path.expanduser(str(path))).resolve()
    if await is_allowed(resolved, ctx):
        return resolved

    try:
        result = await ctx.elicit(
            message=f"The MCP server wants to access:\n{resolved}\n"
                    "This path is outside the allowed directories.",
            schema=AccessGrant,
        )
    except Exception:
        # Client doesn't support elicitation (or it failed): fail closed
        raise PermissionError(
            f"Access to {resolved} denied: it is outside the allowed directories and "
            "the client can't ask the user. Restart with --roots to allow its folder."
        )

    if result.action != "accept":
        raise PermissionError(f"The user denied access to {resolved}. Do not retry.")

    scope = result.data.scope
    if scope == "file":
        allowed_paths.add(resolved)
    elif scope == "folder":
        allowed_dirs.add(resolved.parent)
    elif scope != "once":
        raise PermissionError(f"Access to {resolved} denied: unknown scope {scope!r}")
    return resolved


def get_doc_path(doc_id: str, docs_dir: Path) -> Path:
    expanded = os.path.expanduser(doc_id)
    if os.path.isabs(expanded) or expanded.startswith("~"):
        return Path(expanded)
    # For bare filenames, check default docs dir first
    default = docs_dir / doc_id
    if default.exists():
        return default
    # Search individually allowed files, then allowed directories
    for p in allowed_paths:
        if p.name == doc_id and p.exists():
            return p
    for d in allowed_dirs:
        candidate = d / doc_id
        if candidate.exists():
            return candidate
    # Return default even if not found (existence check happens later)
    return default


def granted_files(docs_dir: Path) -> list[Path]:
    """Files in the docs dir plus everything the user has allowed this session."""
    files = []
    if docs_dir.exists():
        files.extend(f for f in docs_dir.iterdir() if f.is_file())
    for d in allowed_dirs:
        if d.exists() and d != docs_dir:
            files.extend(f for f in d.iterdir() if f.is_file())
    files.extend(p for p in allowed_paths if p.is_file() and p not in files)
    return files


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
  path = await check_access(path, ctx)
  if not path.exists():
      raise ValueError(f"Doc {doc_id} not found")

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
  path = await check_access(path, ctx)
  if path.exists():
      raise ValueError(f"Doc {doc_id} already exists")

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
  path = await check_access(path, ctx)
  if not path.exists():
      raise ValueError(f"Doc {doc_id} not found")

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
  path = await check_access(path, ctx)
  if not path.exists():
      raise ValueError(f"Doc {doc_id} not found")

  await ctx.info(f"Deleting document: {doc_id}...")
  path.unlink()

  await ctx.info("Deleted.")
  return f"Deleted {doc_id}"


# Resource to return all files
@mcp.resource(
  "docs://list",
  mime_type="application/json"
)
async def list_files() -> list[str]:
  docs_dir = await get_docs_dir()
  return [f.name for f in granted_files(docs_dir)]


# Resource to return recently modified files
@mcp.resource(
  "docs://recent",
  mime_type="application/json"
)
async def list_recent() -> list[str]:
  docs_dir = await get_docs_dir()
  files = granted_files(docs_dir)
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
  path = await check_access(path, ctx)
  if not path.exists():
      raise ValueError(f"Doc {filename} not found")
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
