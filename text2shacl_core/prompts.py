# prompts.py
import json
from langchain_core.prompts import ChatPromptTemplate


def load_prompt_from_json(path: str, key: str) -> ChatPromptTemplate:
    """
    Load a ChatPromptTemplate from a JSON file.
    Used for text, table, and vision prompts.

    Expected format:
        data[key] = [{"role": "system"|"user"|"assistant", "content": "..."}, ...]
    """
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    messages = [(entry["role"], entry["content"]) for entry in data[key]]
    return ChatPromptTemplate.from_messages(messages)