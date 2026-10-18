from typing import Any, List, Tuple
from mcp import ClientSession
from mcp.shared.context import RequestContext
from mcp.types import ElicitRequestParams, ElicitResult, Prompt, PromptMessage
import re
from pathlib import Path
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style

from core.chat import Chat
from core.llm import LLMService
from mcp_client import MCPClient


class CommandError(Exception):
    """A /command the user typed can't be run (unknown, or missing arguments)."""


class CliChat(Chat):
    def __init__(
        self,
        doc_client: MCPClient,
        clients: dict[str, MCPClient],
        llm_service: LLMService,
    ):
        super().__init__(clients=clients, llm_service=llm_service)

        self.doc_client: MCPClient = doc_client

    async def list_prompts(self) -> list[Prompt]:
        return await self.doc_client.list_prompts()

    async def list_docs_ids(self) -> list[str]:
        return await self.doc_client.read_resource("docs://list")

    async def get_doc_content(self, doc_id: str) -> str:
        return await self.doc_client.read_resource(f"docs://file/{doc_id}")

    async def get_prompt(
        self, command: str, doc_id: str
    ) -> list[PromptMessage]:
        return await self.doc_client.get_prompt(command, {"doc_id": doc_id})

    async def _extract_resources(self, mentions: list[str]) -> str:
        doc_ids = await self.list_docs_ids()
        mentioned_docs: list[Tuple[str, str]] = []

        for mention in mentions:
            # Bare filename or path. The server asks the user (via elicitation)
            # if the path is outside the allowed directories.
            if mention in doc_ids or "/" in mention or mention.startswith("~"):
                # For bare filenames, use the filename directly; for paths, resolve
                if mention in doc_ids:
                    doc_id = mention
                else:
                    doc_id = str(Path(mention).expanduser().resolve())
                try:
                    result = await self.doc_client.call_tool(
                        "read_doc_contents", {"doc_id": doc_id}
                    )
                    if result and result.content and not result.isError:
                        content = result.content[0].text
                        mentioned_docs.append((mention, content))
                except Exception as e:
                    print(f"Failed to read {mention}: {e}")

        return "".join(
            f'\n<document id="{doc_id}">\n{content}\n</document>\n'
            for doc_id, content in mentioned_docs
        )

    def _find_file_paths(self, query: str) -> list[str]:
        """Detect file path references in the query (~/path, /path/to/file.ext)."""
        pattern = r'(?:~?/[\w./-]+\.\w+)'
        return re.findall(pattern, query)

    async def _process_command(self, query: str) -> bool:
        words = query.split()
        # "/summarize ..." is a command; "/Users/me/notes.txt ..." is a path
        if not query.startswith("/") or "/" in words[0][1:]:
            return False

        command = words[0][1:]
        prompts = {p.name: p for p in await self.list_prompts()}
        if command not in prompts:
            available = ", ".join(f"/{name}" for name in prompts)
            raise CommandError(f"Unknown command /{command}. Available: {available}")

        # Words fill the prompt's arguments in order; the last one takes the
        # rest, so "/convert data.csv plain text" works
        arguments = prompts[command].arguments or []
        values = words[1:]
        if len(arguments) > 1 and len(values) > len(arguments):
            values = values[:len(arguments) - 1] + [" ".join(values[len(arguments) - 1:])]
        args = {arg.name: value for arg, value in zip(arguments, values)}

        missing = [arg.name for arg in arguments if arg.required and arg.name not in args]
        if missing:
            usage = " ".join(f"<{arg.name}>" for arg in arguments)
            raise CommandError(f"Usage: /{command} {usage} (missing {', '.join(missing)})")

        messages = await self.doc_client.get_prompt(command, args)

        self.messages += convert_prompt_messages_to_message_params(messages)
        return True

    async def _process_query(self, query: str):
        if await self._process_command(query):
            return

        mentions = parse_mentions(query, await self.list_docs_ids())
        added_resources = await self._extract_resources(mentions)

        # Detect file paths in the query and try to read them (the server asks
        # the user if needed). Skip paths already handled by _extract_resources
        file_paths = self._find_file_paths(query)
        already_handled = set(mentions)

        for file_path in file_paths:
            if file_path not in already_handled:
                resolved = str(Path(file_path).expanduser().resolve())
                try:
                    result = await self.doc_client.call_tool(
                        "read_doc_contents", {"doc_id": resolved}
                    )
                    if result and result.content and not result.isError:
                        content = result.content[0].text
                        added_resources += f'\n<document id="{file_path}">\n{content}\n</document>\n'
                except Exception:
                    pass

        prompt = f"""
        The user has a question:
        <query>
        {query}
        </query>

        The following context may be useful in answering their question:
        <context>
        {added_resources}
        </context>

        Note the user's query might contain references to documents like "@report.docx". The "@" is only
        included as a way of mentioning the doc. The actual name of the document would be "report.docx".
        If the document content is included in this prompt, you don't need to use an additional tool to read the document.
        Answer the user's question directly and concisely. Start with the exact information they need. 
        Don't refer to or mention the provided context in any way - just use it to inform your answer.
        """

        self.llm_service.add_user_message(self.messages, prompt)


def parse_mentions(query: str, doc_ids: list[str]) -> list[str]:
    """Find @mentions in a query.

    A mention is the longest known doc ID right after the "@" (so names with
    spaces work), otherwise the next word with trailing punctuation stripped.
    An "@" inside a word (like an email address) is not a mention.
    """
    mentions = []
    for match in re.finditer(r'(?:^|(?<=\s))@', query):
        rest = query[match.end():]
        known = [d for d in doc_ids if rest.startswith(d)]
        if known:
            mentions.append(max(known, key=len))
            continue
        word = re.match(r'[^\s@]+', rest)
        if word:
            mentions.append(re.sub(r'[?.!,:;]+$', '', word.group()))
    return mentions


async def cli_elicitation_callback(
    context: RequestContext[ClientSession, Any],
    params: ElicitRequestParams,
) -> ElicitResult:
    """Ask the user to fill an MCP elicitation form in the terminal.

    Used for the server's access prompts, but works for any form made of
    choices (enum), yes/no (boolean) or free-text fields.
    """
    print(f"\n{params.message}")
    if getattr(params, "mode", "form") != "form":
        print("(This client can't open URLs, declining.)")
        return ElicitResult(action="decline")

    session = PromptSession(style=Style.from_dict({"prompt": "#aaaaaa"}))
    content: dict[str, Any] = {}
    try:
        for name, prop in params.requestedSchema.get("properties", {}).items():
            title = prop.get("title", name)
            if "enum" in prop:
                choices = prop["enum"]
                labels = prop.get("enumNames", choices)
                options = ", ".join(f"{i}={label}" for i, label in enumerate(labels, 1))
                deny = len(choices) + 1
                answer = (await session.prompt_async(
                    f"{title}? ({options}, {deny}=Deny): "
                )).strip()
                if not answer.isdigit() or not 1 <= int(answer) <= len(choices):
                    return ElicitResult(action="decline")
                content[name] = choices[int(answer) - 1]
            elif prop.get("type") == "boolean":
                answer = (await session.prompt_async(f"{title}? (y/n): ")).strip().lower()
                content[name] = answer in ("y", "yes")
            else:
                content[name] = await session.prompt_async(f"{title}: ")
    except (KeyboardInterrupt, EOFError):
        return ElicitResult(action="cancel")

    return ElicitResult(action="accept", content=content)


def convert_prompt_message_to_message_param(
    prompt_message: "PromptMessage",
) -> dict:
    role = "user" if prompt_message.role == "user" else "assistant"

    content = prompt_message.content

    # Check if content is a dict-like object with a "type" field
    if isinstance(content, dict) or hasattr(content, "__dict__"):
        content_type = (
            content.get("type", None)
            if isinstance(content, dict)
            else getattr(content, "type", None)
        )
        if content_type == "text":
            content_text = (
                content.get("text", "")
                if isinstance(content, dict)
                else getattr(content, "text", "")
            )
            return {"role": role, "content": content_text}

    if isinstance(content, list):
        text_blocks = []
        for item in content:
            # Check if item is a dict-like object with a "type" field
            if isinstance(item, dict) or hasattr(item, "__dict__"):
                item_type = (
                    item.get("type", None)
                    if isinstance(item, dict)
                    else getattr(item, "type", None)
                )
                if item_type == "text":
                    item_text = (
                        item.get("text", "")
                        if isinstance(item, dict)
                        else getattr(item, "text", "")
                    )
                    text_blocks.append({"type": "text", "text": item_text})

        if text_blocks:
            return {"role": role, "content": text_blocks}

    return {"role": role, "content": ""}


def convert_prompt_messages_to_message_params(
    prompt_messages: List[PromptMessage],
) -> List[dict]:
    return [
        convert_prompt_message_to_message_param(msg) for msg in prompt_messages
    ]
