from core.llm import LLMService
from mcp_client import MCPClient
from core.tools import ToolManager


class Chat:
    def __init__(self, llm_service: LLMService, clients: dict[str, MCPClient]):
        self.llm_service: LLMService = llm_service
        self.clients: dict[str, MCPClient] = clients
        self.messages: list = []

    async def _process_query(self, query: str):
        self.llm_service.add_user_message(self.messages, query)

    async def run(
        self,
        query: str,
    ) -> str:
        # If this turn fails partway, drop its messages so the next turn
        # starts from a clean conversation
        turn_start = len(self.messages)
        try:
            return await self._run_turn(query)
        except BaseException:
            del self.messages[turn_start:]
            raise

    async def _run_turn(self, query: str) -> str:
        final_text_response = ""

        await self._process_query(query)

        while True:
            response = self.llm_service.chat(
                messages=self.messages,
                tools=await ToolManager.get_all_tools(self.clients),
            )

            message = response.choices[0].message

            if message.tool_calls:
                assistant_message = {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in message.tool_calls
                    ],
                }
                self.messages.append(assistant_message)

                tool_results = await ToolManager.execute_tool_requests(
                    self.clients, response
                )
                self.messages.extend(tool_results)
            else:
                self.llm_service.add_assistant_message(self.messages, message.content or "")
                final_text_response = message.content or ""
                break

        return final_text_response
