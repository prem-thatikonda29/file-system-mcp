import openai
from openai import OpenAI
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam


def describe_llm_error(error: openai.APIError, model: str, base_url: str) -> str:
    """Turn an OpenAI SDK error into a short message the user can act on."""
    if isinstance(error, openai.AuthenticationError):
        return f"The API key was rejected by {base_url}. Check OPENAI_API_KEY in .env."
    if isinstance(error, openai.PermissionDeniedError):
        return f"This API key isn't allowed to use model '{model}' at {base_url}."
    if isinstance(error, openai.NotFoundError):
        return f"Model '{model}' wasn't found at {base_url}. Check OPENAI_MODEL in .env."
    if isinstance(error, openai.RateLimitError):
        return "Rate limited or out of quota. Wait a moment, or check your plan and billing."
    if isinstance(error, openai.APITimeoutError):
        return f"The request to {base_url} timed out. Try again."
    if isinstance(error, openai.APIConnectionError):
        return f"Couldn't connect to {base_url}. Check your network and OPENAI_BASE_URL."
    if isinstance(error, openai.APIStatusError):
        return f"The LLM API returned an error ({error.status_code}): {error.message}"
    return f"The LLM API returned an error: {error}"


class LLMService:
    def __init__(self, model: str):
        self.client = OpenAI()
        self.model = model

    def describe_error(self, error: openai.APIError) -> str:
        return describe_llm_error(error, self.model, str(self.client.base_url))

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
