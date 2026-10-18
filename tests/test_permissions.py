"""Permission flow tests against the real MCP server over stdio.

The model's tool calls are simulated with fake ChatCompletion objects, so no
API key is needed. The user's answers to access prompts are scripted.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from mcp.types import ElicitResult

from core.cli_chat import parse_mentions
from core.tools import ToolManager
from mcp_client import MCPClient

SERVER = str(Path(__file__).resolve().parent.parent / "mcp_server.py")

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def outside(tmp_path):
    """A folder outside the allowed directories, with a few files in it."""
    (tmp_path / "secret.txt").write_text("TOP SECRET")
    (tmp_path / "sibling.txt").write_text("SIBLING DATA")
    (tmp_path / "victim.txt").write_text("keep me")
    return tmp_path


class ScriptedUser:
    """Elicitation callback that answers access prompts from a script."""

    def __init__(self, *answers: str):
        # Each answer is a scope ("once", "file", "folder") or "deny"
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def __call__(self, context, params):
        self.prompts.append(params.message)
        answer = self.answers.pop(0) if self.answers else "deny"
        if answer == "deny":
            return ElicitResult(action="decline")
        return ElicitResult(action="accept", content={"scope": answer})


def connect(user=None):
    return MCPClient(command=sys.executable, args=[SERVER], elicitation_callback=user)


def model_calls(*calls):
    """A fake ChatCompletion in which the model asks for these tool calls."""
    tool_calls = [
        NS(id=f"call_{i}", function=NS(name=name, arguments=json.dumps(args)))
        for i, (name, args) in enumerate(calls)
    ]
    return NS(choices=[NS(message=NS(tool_calls=tool_calls))])


async def run_model_calls(client, *calls) -> list[str]:
    """Run tool calls the way the chat loop does and return what the model sees."""
    results = await ToolManager.execute_tool_requests({"doc": client}, model_calls(*calls))
    return [r["content"] for r in results]


async def read(client, path) -> tuple[bool, str]:
    result = await client.call_tool("read_doc_contents", {"doc_id": str(path)})
    return result.isError, result.content[0].text


async def test_model_is_not_offered_a_way_to_grant_access():
    async with connect(ScriptedUser()) as client:
        names = [t["function"]["name"] for t in await ToolManager.get_all_tools({"doc": client})]
    assert "allow_path" not in names


async def test_files_inside_roots_need_no_prompt():
    user = ScriptedUser()
    async with connect(user) as client:
        is_error, text = await read(client, "pokemon.md")
    assert not is_error and text
    assert user.prompts == []


async def test_model_tool_call_outside_roots_asks_the_user(outside):
    user = ScriptedUser("once")
    async with connect(user) as client:
        [seen] = await run_model_calls(
            client, ("read_doc_contents", {"doc_id": str(outside / "secret.txt")})
        )
    assert len(user.prompts) == 1 and "secret.txt" in user.prompts[0]
    assert "TOP SECRET" in seen


async def test_denied_delete_leaves_the_file_alone(outside):
    user = ScriptedUser("deny")
    async with connect(user) as client:
        [seen] = await run_model_calls(
            client, ("delete_doc", {"doc_id": str(outside / "victim.txt")})
        )
    assert "denied" in seen
    assert (outside / "victim.txt").exists()


async def test_allow_once_only_covers_one_request(outside):
    user = ScriptedUser("once", "deny", "deny")
    async with connect(user) as client:
        assert await read(client, outside / "secret.txt") == (False, "TOP SECRET")
        assert (await read(client, outside / "secret.txt"))[0]
        assert (await read(client, outside / "sibling.txt"))[0]
    assert len(user.prompts) == 3


async def test_always_allow_file_does_not_open_its_folder(outside):
    user = ScriptedUser("file", "deny")
    async with connect(user) as client:
        assert await read(client, outside / "secret.txt") == (False, "TOP SECRET")
        assert await read(client, outside / "secret.txt") == (False, "TOP SECRET")
        assert (await read(client, outside / "sibling.txt"))[0]
        # The granted file is also reachable by bare name and listed for @-completion
        assert await read(client, "secret.txt") == (False, "TOP SECRET")
        listed = await client.read_resource("docs://list")
    assert len(user.prompts) == 2
    assert "secret.txt" in listed and "sibling.txt" not in listed


async def test_always_allow_folder_covers_siblings(outside):
    user = ScriptedUser("folder")
    async with connect(user) as client:
        assert await read(client, outside / "secret.txt") == (False, "TOP SECRET")
        assert await read(client, outside / "sibling.txt") == (False, "SIBLING DATA")
    assert len(user.prompts) == 1


async def test_cannot_probe_which_paths_exist(outside):
    user = ScriptedUser("deny", "deny")
    async with connect(user) as client:
        exists_error, exists_text = await read(client, outside / "secret.txt")
        missing_error, missing_text = await read(client, outside / "nope.txt")
    assert exists_error and missing_error
    assert "denied" in exists_text and "denied" in missing_text
    assert "not found" not in missing_text


async def test_client_without_elicitation_fails_closed(outside):
    async with connect(user=None) as client:
        is_error, text = await read(client, outside / "secret.txt")
    assert is_error and "can't ask the user" in text


@pytest.mark.parametrize(
    "query, expected",
    [
        ("Summarize @pokemon.md", ["pokemon.md"]),
        ("Summarize @pokemon.md please", ["pokemon.md"]),
        ("compare @pokemon.md and @pokemon.json?", ["pokemon.md", "pokemon.json"]),
        ("What's in @Family Tree.pdf for me", ["Family Tree.pdf"]),
        ("read @~/notes/todo.txt, thanks", ["~/notes/todo.txt"]),
        ("email me at me@example.com", []),
    ],
)
def test_parse_mentions(query, expected):
    doc_ids = ["pokemon.md", "pokemon.json", "Family Tree.pdf"]
    assert parse_mentions(query, doc_ids) == expected
