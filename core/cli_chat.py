from typing import List, Tuple
from mcp.types import Prompt, PromptMessage
import re
from pathlib import Path
from prompt_toolkit import PromptSession
from prompt_toolkit.styles import Style

from core.chat import Chat
from core.llm import LLMService
from mcp_client import MCPClient


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

    async def _request_access(self, path: str) -> bool:
        """Prompt user for access permission. Returns True if allowed."""
        print(f"\nThe MCP server wants to access:\n{path}")
        print("This path is outside the allowed directories.")

        session = PromptSession(style=Style.from_dict({"prompt": "#aaaaaa"}))
        choice = await session.prompt_async(
            "Allow access? (1=Allow Once, 2=Always Allow, 3=Deny): "
        )

        choice = choice.strip()
        if choice in ("1", "2"):
            try:
                await self.doc_client.call_tool("allow_path", {"path": path})
            except Exception as e:
                print(f"Failed to grant access: {e}")
                return False
            return True
        return False

    async def _call_tool_with_permissions(
        self, tool_name: str, tool_input: dict
    ) -> any:
        """Call a tool, handling ACCESS_REQUIRED errors with permission prompts."""
        result = await self.doc_client.call_tool(tool_name, tool_input)
        if result and result.isError and result.content:
            error_text = result.content[0].text
            if "ACCESS_REQUIRED:" in error_text:
                path = error_text.split("ACCESS_REQUIRED:", 1)[1]
                if await self._request_access(path):
                    return await self.doc_client.call_tool(tool_name, tool_input)
        return result

    async def _extract_resources(self, query: str) -> str:
        # Find all @mentions — handle spaces in filenames by taking everything
        # after @ until end of query or another @
        raw_mentions = re.findall(r'@([^\s@]+(?:\s+[^\s@]+)*)', query)
        # Strip trailing punctuation (?, ., ,, !, :, ;)
        mentions = [re.sub(r'[?.!,:;]+$', '', m) for m in raw_mentions]

        doc_ids = await self.list_docs_ids()
        mentioned_docs: list[Tuple[str, str]] = []

        for mention in mentions:
            # Bare filename or path — read via tool (handles permission check)
            if mention in doc_ids or "/" in mention or mention.startswith("~"):
                # For bare filenames, use the filename directly; for paths, resolve
                if mention in doc_ids:
                    doc_id = mention
                else:
                    doc_id = str(Path(mention).expanduser().resolve())
                try:
                    result = await self._call_tool_with_permissions(
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
        if not query.startswith("/"):
            return False

        words = query.split()
        command = words[0].replace("/", "")

        messages = await self.doc_client.get_prompt(
            command, {"doc_id": words[1]}
        )

        self.messages += convert_prompt_messages_to_message_params(messages)
        return True

    async def _process_query(self, query: str):
        if await self._process_command(query):
            return

        added_resources = await self._extract_resources(query)

        # Detect file paths in the query and try to read them (triggers permission check)
        # Skip paths already handled by _extract_resources
        file_paths = self._find_file_paths(query)
        already_handled = set()
        raw_mentions = re.findall(r'@([^\s@]+(?:\s+[^\s@]+)*)', query)
        for m in raw_mentions:
            clean = re.sub(r'[?.!,:;]+$', '', m)
            if "/" in clean or clean.startswith("~"):
                already_handled.add(clean)

        for file_path in file_paths:
            if file_path not in already_handled:
                resolved = str(Path(file_path).expanduser().resolve())
                try:
                    result = await self._call_tool_with_permissions(
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
