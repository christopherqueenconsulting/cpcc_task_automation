"""Builder measurement for the Solution tab payload (PR 488).

Usage: PYTHONPATH=src python scripts/measure_solution_tab_payload.py
Renders the plan-less Solution tab with a synthetic ~78 KB solution and 30 synthetic results in
AppTest, and prints the rendered st.code size and the pickled sizes of the related state."""
import pickle
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests" / "unit"))
import test_solution_tab as t  # noqa: E402
from cqc_cpcc.execution_check import SolutionSources, SourceFile  # noqa: E402
from cqc_cpcc.utilities import program_runner as pr  # noqa: E402
from cqc_streamlit_app import execution_view as ev  # noqa: E402

ev.on_community_cloud = lambda: False
pr.runner_status = lambda: pr.RunnerStatus(True)
big = "int main() {\n" + "".join(f'  puts("row {i} of the solution");\n' for i in range(2200)) + "}\n"
ref = t._bundle(("main.cpp", "cpp", big))
results = [(f"10{i} - Student {i}", t._result()) for i in range(30)]
at = t._tab_app(results, reference=ref).run()
src = SolutionSources(language="cpp", files=(SourceFile("main.cpp", big),), main="main.cpp")
print("solution chars", len(big))
print("rendered st.code chars", sum(len(c.value) for c in at.code), "blocks", len(at.code))
print("pickle reference_solution", len(pickle.dumps(ref)))
print("pickle SolutionSources", len(pickle.dumps(src)))
print("pickle 30 results", len(pickle.dumps(results)))
