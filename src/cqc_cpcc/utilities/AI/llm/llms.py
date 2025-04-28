#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

from langchain_core.language_models import BaseChatModel
from langchain_core.runnables import RunnableSerializable
from langchain_openai import ChatOpenAI


def get_default_llm_model() -> str:
    # model = 'gpt-3.5-turbo-1106'
    # model = 'gpt-4-1106-preview'
    model = 'gpt-3.5-turbo' # Deprecated 'gpt-3.5-turbo-16k-0613'
    # model = 'gpt-4-turbo'
    # model = "gpt-4"
    # model = "gpt-4o"
    return model

def get_default_retry_model() -> str:
    model = 'gpt-3.5-turbo' # Deprecated 'gpt-3.5-turbo-16k-0613'
    return model


def get_default_llm() -> BaseChatModel:
    model = get_default_llm_model()
    # model = "gpt-4"
    temperature = .2  # .2 <- More deterministic | More Creative -> .8
    default_llm = ChatOpenAI(temperature=temperature, model=model)
    return default_llm


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
            #print("Model (from completion_chain): %s" % step.model_name )
            llm_model = step.model_name
            break

    if llm_model is None:
        #raise ValueError("No LLM found in the RunnableSerializable steps.")
        llm_model = get_default_llm_model()
    return llm_model
