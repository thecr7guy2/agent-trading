from src.agents.providers.claude import ClaudeProvider
from src.config import get_settings


def get_provider() -> ClaudeProvider:
    settings = get_settings()
    return ClaudeProvider(api_key=settings.anthropic_api_key)
