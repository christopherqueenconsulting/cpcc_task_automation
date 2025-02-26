#  Copyright (c) 2024. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)
import logging

from cqc_cpcc.utilities.logger import logger, fmt


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
logger.addHandler(streamlit_handler)

# Set the desired logging level
logger.setLevel(logging.DEBUG)
