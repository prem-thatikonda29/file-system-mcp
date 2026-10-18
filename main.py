import asyncio
import sys
import os
import argparse
from dotenv import load_dotenv
from contextlib import AsyncExitStack

from mcp_client import MCPClient
from core.llm import LLMService

from core.cli_chat import CliChat, cli_elicitation_callback
from core.cli import CliApp

load_dotenv()

# OpenAI Config
openai_model = os.getenv("OPENAI_MODEL", "")
openai_api_key = os.getenv("OPENAI_API_KEY", "")


assert openai_model, "Error: OPENAI_MODEL cannot be empty. Update .env"
assert openai_api_key, (
    "Error: OPENAI_API_KEY cannot be empty. Update .env"
)


def parse_args():
    parser = argparse.ArgumentParser(description="MCP CLI Document Manager")
    parser.add_argument(
        "--roots", "-r",
        nargs="+",
        default=[],
        help="Root directories the server can access (e.g. ~/mcp_docs ./data)"
    )
    parser.add_argument(
        "server_scripts",
        nargs="*",
        help="Additional MCP server scripts to load"
    )
    return parser.parse_args()


async def main():
    args = parse_args()
    roots = [os.path.expanduser(r) for r in args.roots]
    llm_service = LLMService(model=openai_model)
    clients = {}

    command, cmd_args = (
        ("uv", ["run", "mcp_server.py"])
        if os.getenv("USE_UV", "0") == "1"
        else ("python", ["mcp_server.py"])
    )

    async with AsyncExitStack() as stack:
        doc_client = await stack.enter_async_context(
            MCPClient(
                command=command,
                args=cmd_args,
                roots=roots,
                elicitation_callback=cli_elicitation_callback,
            )
        )
        clients["doc_client"] = doc_client

        for i, server_script in enumerate(args.server_scripts):
            client_id = f"client_{i}_{server_script}"
            client = await stack.enter_async_context(
                MCPClient(
                    command="uv",
                    args=["run", server_script],
                    elicitation_callback=cli_elicitation_callback,
                )
            )
            clients[client_id] = client

        chat = CliChat(
            doc_client=doc_client,
            clients=clients,
            llm_service=llm_service,
        )

        cli = CliApp(chat)
        await cli.initialize()
        await cli.run()


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(main())
