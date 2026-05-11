#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

import threading
from typing import Any, TypeVar, cast

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult
from streamlit.elements.lib.mutable_status_container import StatusContainer
from streamlit.runtime.scriptrunner_utils.script_run_context import (
    SCRIPT_RUN_CONTEXT_ATTR_NAME,
    get_script_run_ctx,
)

T = TypeVar("T")


class ChatGPTStatusCallbackHandler(BaseCallbackHandler):

    def __init__(
            self,
            status_container: StatusContainer,
            prefix_label: str = None
    ):
        self._status_container = status_container
        if prefix_label is None:
            self._prefix_label = ""
        else:
            self._prefix_label = prefix_label + " | "

    def with_streamlit_context(self, fn: T) -> T:
        """Fix bug in streamlit which raises streamlit.errors.NoSessionContext."""
        ctx = get_script_run_ctx()

        if ctx is None:
            # Allow usage outside Streamlit (tests, CLI scripts) without raising.
            def _cb(*args: Any, **kwargs: Any) -> Any:
                return fn(*args, **kwargs)

            return cast(T, _cb)

        def _cb(*args: Any, **kwargs: Any) -> Any:
            """Do it."""

            thread = threading.current_thread()
            do_nothing = hasattr(thread, SCRIPT_RUN_CONTEXT_ATTR_NAME) and (
                    getattr(thread, SCRIPT_RUN_CONTEXT_ATTR_NAME) == ctx
            )

            if not do_nothing:
                setattr(thread, SCRIPT_RUN_CONTEXT_ATTR_NAME, ctx)

            # Call the callback.
            ret = fn(*args, **kwargs)

            if not do_nothing:
                # Why delattr? Because tasks for different users may be done by
                # the same thread at different times. Danger danger.
                delattr(thread, SCRIPT_RUN_CONTEXT_ATTR_NAME)
            return ret

        return cast(T, _cb)

    @with_streamlit_context
    def on_llm_start(
            self, serialized: dict[str, Any], prompts: list[str], **kwargs: Any
    ) -> None:
        self._status_container.update(label=self._prefix_label + "Getting response from ChatGPT")

    @with_streamlit_context
    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        # self._status_container.update(label=self._prefix_label + "From ChatGPT: " + str(response))
        self._status_container.update(label=self._prefix_label + "ChatGPT Finished")

    @with_streamlit_context
    def on_llm_error(self, error: BaseException, *args: Any, **kwargs: Any) -> None:
        self._status_container.update(label=self._prefix_label + "ChatGPT Error: " + str(error))
