#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""A local stand-in for OpenRouter, for checking timeouts and progress by hand.

Any model whose id contains ``slow`` never answers; every other model answers at once with
the app's deterministic test-mode response for the requested schema. No network, no key.

    poetry run python tests/fixtures/stub_openrouter.py --port 8765
    OPENROUTER_BASE_URL=http://127.0.0.1:8765/api/v1 OPENROUTER_API_KEY=stub \\
        CQC_LLM_TIMEOUT_GRADING=10 poetry run streamlit run src/cqc_streamlit_app/Home.py

Pick ``stub/slow`` as the grading model to watch attempts time out and the fallback start,
``stub/fast`` for an immediate answer.
"""

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from cqc_cpcc.exam_review import ErrorDefinitions
from cqc_cpcc.requirement_coverage import RequirementChecklist
from cqc_cpcc.rubric_models import RubricAssessmentResult
from cqc_cpcc.utilities.AI.openai_client import _get_test_mode_response

SCHEMAS = {m.__name__: m for m in (RubricAssessmentResult, RequirementChecklist, ErrorDefinitions)}
MODELS = ["stub/slow", "stub/fast"]
_never = threading.Event()


class StubOpenRouter(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 - http.server API
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"data": [{"id": m, "name": m, "context_length": 128000} for m in MODELS]})
        else:
            self._send(404, {"error": {"message": "not found"}})

    def do_POST(self):  # noqa: N802 - http.server API
        request = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        model = request.get("model", "")
        if "slow" in model:
            _never.wait()  # hang until the client gives up
            return
        name = request.get("response_format", {}).get("json_schema", {}).get("name")
        schema = SCHEMAS.get(name)
        if schema is None:
            self._send(400, {"error": {"message": f"stub has no canned response for {name!r}"}})
            return
        content = _get_test_mode_response(schema).model_dump_json()
        self._send(200, {
            "id": "gen-stub", "object": "chat.completion", "created": 0, "model": model,
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })

    def log_message(self, fmt, *args):  # quiet: one line per request
        print(f"stub_openrouter: {self.command} {self.path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), StubOpenRouter)
    server.daemon_threads = True
    print(f"stub_openrouter on http://127.0.0.1:{args.port}/api/v1 (models: {', '.join(MODELS)})")
    server.serve_forever()


if __name__ == "__main__":
    main()
