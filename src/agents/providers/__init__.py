from src.agents.providers.claude import ClaudeProvider
from src.agents.providers.minimax import MiniMaxProvider
from src.config import get_settings
from src.db.models import LLMProvider


def get_provider(llm: LLMProvider) -> ClaudeProvider | MiniMaxProvider:
    settings = get_settings()
    if llm in (LLMProvider.CLAUDE, LLMProvider.CLAUDE_AGGRESSIVE):
        return ClaudeProvider(api_key=settings.anthropic_api_key)
    return MiniMaxProvider(
        api_key=settings.minimax_api_key,
        base_url=settings.minimax_base_url,
    )
