#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import logging

from cqc_cpcc.utilities.logger import fmt, logger
from cqc_cpcc.utilities.pii_redaction import install_log_redaction


class StreamlitHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.logs = []

    def emit(self, record):
        log_entry = self.format(record)
        self.logs.append(log_entry)
        # Limit logs to the last 100 entries
        self.logs = self.logs[-100:]

    def get_logs(self):
        return "\n".join(self.logs)


# Streamlit logging handler
streamlit_handler = StreamlitHandler()
streamlit_handler.setFormatter(fmt)
install_log_redaction(streamlit_handler)
logger.addHandler(streamlit_handler)

# The level comes from the environment (see logger._resolve_base_log_level). This
# module used to force DEBUG, which also turned on DEBUG for the log *file* -- and the
# DEBUG lines include submission contents.
