#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

from typing import Optional

from cqc_cpcc.utilities.AI import model_registry
from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableSerializable
from langchain_openai import ChatOpenAI


def get_default_llm_model() -> str:
    """Model id for the "feedback" role from config/model_registry.json."""
    return model_registry.resolve("feedback").model


def get_default_retry_model() -> str:
    """Model for LangChain parser retries: the "flowgorithm" role's fallback."""
    resolved = model_registry.resolve("flowgorithm")
    return resolved.fallback or resolved.model


def get_openrouter_chat_model(
        role: model_registry.Role = "flowgorithm",
        override: Optional[str] = None,
        temperature: Optional[float] = None,
        api_key: Optional[str] = None,
) -> ChatOpenAI:
    """LangChain chat model pointed at OpenRouter, configured from the model registry.

    ``temperature`` is sent only when the registry says the model accepts it.
    """
    from cqc_cpcc.utilities.AI import openrouter_client

    resolved = model_registry.resolve(role, override=override)
    kwargs = {
        "model": resolved.model,
        "base_url": openrouter_client.OPENROUTER_BASE_URL,
        "api_key": api_key or openrouter_client._get_openrouter_api_key(),
        "use_responses_api": False,
        "extra_body": model_registry.build_request_params(resolved),
        "default_headers": {
            "X-Title": openrouter_client.OPENROUTER_APP_NAME,
            "HTTP-Referer": openrouter_client.OPENROUTER_APP_URL,
        },
    }
    if temperature is not None and model_registry.supports_temperature(resolved.model):
        kwargs["temperature"] = temperature
    return ChatOpenAI(**kwargs)


def get_default_llm() -> BaseChatModel:
    return get_openrouter_chat_model("grading", temperature=.2)


def get_model_from_chat_model(chat_model: BaseChatModel):
    # print("Model (from llm): %s" % chat_model.dict().get('model'))
    return chat_model.dict().get('model')


def get_temperature_from_chat_model(chat_model: BaseChatModel):
    # print("Model (from llm): %s" % chat_model.dict().get('temperature'))
    return chat_model.dict().get('temperature')


def get_llm_model_from_runnable_serializable(completion_chain: RunnableSerializable) -> str:
    # Extract the LLM from the RunnableSerializable (completion_chain)
    llm_model = None
    for step in completion_chain.steps:
        if isinstance(step, BaseChatModel):  # Check if the step is an LLM
            # print("Model (from completion_chain): %s" % step.model_name )
            llm_model = step.model_name
            break

    if llm_model is None:
        # raise ValueError("No LLM found in the RunnableSerializable steps.")
        llm_model = get_default_llm_model()
    return llm_model
