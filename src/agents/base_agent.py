from abc import ABC, abstractmethod

from pydantic import BaseModel

from src.db.models import AgentStage, LLMProvider


class BaseAgent(ABC):
    @abstractmethod
    async def run(self, input_data: BaseModel) -> BaseModel:
        """Execute this agent stage and return typed output."""

    @property
    @abstractmethod
    def provider(self) -> LLMProvider:
        """Which LLM provider this agent uses."""

    @property
    @abstractmethod
    def stage(self) -> AgentStage:
        """Which pipeline stage this agent handles."""

    @property
    def name(self) -> str:
        return f"{self.provider}-{self.stage}"
