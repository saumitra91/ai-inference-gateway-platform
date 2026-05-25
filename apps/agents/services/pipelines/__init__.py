from apps.agents.services.pipelines.research import ResearchPipeline
from apps.agents.services.pipelines.job_discovery import JobDiscoveryWorkflow
from apps.agents.services.telegram.service import TelegramService

__all__ = [
    "ResearchPipeline",
    "JobDiscoveryWorkflow",
]

PIPELINE_MAP = {
    "market_research": ResearchPipeline,
    "job_discovery": JobDiscoveryWorkflow,
}


def get_pipeline(agent_type: str, llm_backend: str = "llamacpp", telegram: TelegramService | None = None):
    cls = PIPELINE_MAP.get(agent_type)
    if cls is None:
        raise ValueError(f"Unknown agent type: {agent_type}")
    return cls(telegram=telegram, llm_backend=llm_backend)
