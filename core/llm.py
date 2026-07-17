from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam


class LLMService:
    def __init__(self, model: str):
        self.client = OpenAI()
        self.model = model

    def add_user_message(self, messages: list, message):
        user_message: ChatCompletionMessageParam = {
            "role": "user",
            "content": message if isinstance(message, str) else str(message),
        }
        messages.append(user_message)

    def add_assistant_message(self, messages: list, message):
        assistant_message: ChatCompletionMessageParam = {
            "role": "assistant",
            "content": message if isinstance(message, str) else str(message),
        }
        messages.append(assistant_message)

    def add_tool_result_message(self, messages: list, tool_call_id: str, content: str, is_error: bool = False):
        tool_result_message: ChatCompletionMessageParam = {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        }
        messages.append(tool_result_message)

    def text_from_message(self, message: ChatCompletion) -> str:
        return message.choices[0].message.content or ""

    def chat(
        self,
        messages,
        system=None,
        temperature=1.0,
        stop_sequences=[],
        tools=None,
        thinking=False,
        thinking_budget=1024,
    ) -> ChatCompletion:
        params = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }

        if stop_sequences:
            params["stop"] = stop_sequences

        if tools:
            params["tools"] = tools

        if system:
            params["messages"] = [{"role": "system", "content": system}] + messages

        message = self.client.chat.completions.create(**params)
        return message
