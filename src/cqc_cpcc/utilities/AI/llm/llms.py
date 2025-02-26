#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI


def get_default_llm()->BaseChatModel:

    # model = 'gpt-3.5-turbo-1106'
    # model = 'gpt-4-1106-preview'
    model = 'gpt-3.5-turbo-16k-0613'
    # model = "gpt-4"
    temperature = .2  # .2 <- More deterministic | More Creative -> .8
    default_llm = ChatOpenAI(temperature=temperature, model=model)
    return default_llm


def get_model_from_chat_model(chat_model: BaseChatModel):
    #print("Model (from llm): %s" % chat_model.dict().get('model'))
    return chat_model.dict().get('model')

def get_temperature_from_chat_model(chat_model: BaseChatModel):
    #print("Model (from llm): %s" % chat_model.dict().get('temperature'))
    return chat_model.dict().get('temperature')