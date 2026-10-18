"""LLM API failures should leave the chat usable, with a message the user can act on."""
import sys
from pathlib import Path
from types import SimpleNamespace as NS

import httpx
import openai
import pytest

from core.chat import Chat
from core.cli_chat import CliChat, CommandError
from core.llm import describe_llm_error
from mcp_client import MCPClient

SERVER = str(Path(__file__).resolve().parent.parent / "mcp_server.py")

URL = "https://api.example.com/v1/"


def status_error(cls, code):
    request = httpx.Request("POST", URL + "chat/completions")
    return cls("boom", response=httpx.Response(code, request=request), body=None)


def connect():
    return MCPClient(command=sys.executable, args=[SERVER])


class FlakyLLM:
    """Fails the first call, then answers with plain text."""

    def __init__(self, error):
        self.error = error

    def add_user_message(self, messages, text):
        messages.append({"role": "user", "content": text})

    def add_assistant_message(self, messages, text):
        messages.append({"role": "assistant", "content": text})

    def chat(self, messages, tools=None):
        if self.error:
            error, self.error = self.error, None
            raise error
        return NS(choices=[NS(message=NS(content="hi there", tool_calls=None))])


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_failed_turn_is_rolled_back(anyio_backend):
    chat = Chat(llm_service=FlakyLLM(status_error(openai.AuthenticationError, 401)), clients={})

    with pytest.raises(openai.AuthenticationError):
        await chat.run("hello")
    assert chat.messages == []

    assert await chat.run("hello again") == "hi there"
    assert [m["content"] for m in chat.messages] == ["hello again", "hi there"]


@pytest.mark.parametrize(
    "error, expected",
    [
        (status_error(openai.AuthenticationError, 401), "Check OPENAI_API_KEY"),
        (status_error(openai.NotFoundError, 404), "Model 'gpt-x' wasn't found"),
        (status_error(openai.RateLimitError, 429), "Rate limited"),
        (status_error(openai.InternalServerError, 500), "error (500)"),
        (openai.APIConnectionError(request=httpx.Request("POST", URL)), "Couldn't connect"),
    ],
)
def test_describe_llm_error(error, expected):
    assert expected in describe_llm_error(error, "gpt-x", URL)


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
@pytest.mark.parametrize(
    "query, expected",
    [
        ("/summarize", "Usage: /summarize <doc_id> (missing doc_id)"),
        ("/rewrite pokemon.md", "Usage: /rewrite <doc_id> <tone> (missing tone)"),
        ("/nosuchcommand pokemon.md", "Unknown command /nosuchcommand. Available: /format"),
    ],
)
async def test_bad_commands_give_usage(anyio_backend, query, expected):
    async with connect() as client:
        chat = CliChat(doc_client=client, clients={"doc": client}, llm_service=FlakyLLM(None))
        with pytest.raises(CommandError) as raised:
            await chat._process_command(query)
    assert str(raised.value).startswith(expected)


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio"])
async def test_command_arguments_reach_the_prompt(anyio_backend):
    async with connect() as client:
        chat = CliChat(doc_client=client, clients={"doc": client}, llm_service=FlakyLLM(None))
        assert await chat._process_command("/convert pokemon.csv plain text")
        assert not await chat._process_command("/Users/me/notes.txt what is this?")
    prompt = chat.messages[0]["content"]
    assert "pokemon.csv" in prompt and "plain text" in prompt
