#!/usr/bin/env python
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""READ-ONLY probe of the BrightSpace grade write-back targets.

Opens a real assignment (or quiz) the same way the Grade Assignment page's write-back
does, then visits the evaluation page of the first ``--learners`` students and records:

- what the product's own locators decide (score input, feedback editor, Save Draft,
  Attach control, the "Add Feedback Files" bulk-import button);
- an inventory of every candidate control on the page (inputs with their aria-labels,
  rich-text editors, buttons, rubric criteria and their level labels, selects).

It never types, clicks Save/Publish/Update, uploads, or selects a rubric level. It only
navigates, and (quiz route) switches the attempt view select, which saves nothing.

Learner names are replaced with ``learner_<n>`` and no grades, feedback or submission
text are read. The JSON report goes to ``output/writeback_probe/`` (gitignored).

    HEADLESS_BROWSER=false BROWSER_TYPE=DOCKER_CHROME DOCKER_TYPE=LOCAL \\
      poetry run python scripts/brightspace_writeback_probe.py --url "<assignment URL>"
"""

import argparse
import datetime as _dt
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from cqc_cpcc.utilities import brightspace_writeback as wb  # noqa: E402
from cqc_cpcc.utilities.brightspace_submissions import ROUTE_QUIZ, detect_route  # noqa: E402

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output", "writeback_probe")

# Deep walk in document order across shadow roots (not iframes: values there are
# submission/feedback bodies we do not want to read).
_INVENTORY_JS = r"""
function* deep(root){
  const s=[root.documentElement||root];
  while(s.length){
    const n=s.pop(); if(!n)continue; yield n;
    const k=[...(n.children||[])];
    for(let i=k.length-1;i>=0;i--)s.push(k[i]);
    if(n.shadowRoot)s.push(n.shadowRoot);
  }
}
function host(el){const r=el.getRootNode&&el.getRootNode();return (r&&r.host)?(r.host.tagName||'').toLowerCase():'';}
function vis(el){try{const r=el.getBoundingClientRect();return r.width>0&&r.height>0;}catch(e){return false;}}
function a(el,n){return (el.getAttribute&&el.getAttribute(n))||'';}
function short(t){return (t||'').replace(/\s+/g,' ').trim().slice(0,80);}
const out={inputs:[],editors:[],buttons:[],rubric:[],rubric_tags:{},selects:[],attach:[],dialogs:[]};
for(const el of deep(document)){
  const t=(el.tagName||'').toLowerCase();
  if(t==='input'||t==='d2l-input-number'||t==='d2l-input-text'||t==='textarea'){
    if(['hidden','checkbox','radio','file'].includes(a(el,'type')))continue;
    out.inputs.push({tag:t,type:a(el,'type'),aria_label:a(el,'aria-label'),label:a(el,'label'),
      name:a(el,'name'),id:a(el,'id'),host:host(el),visible:vis(el),has_value:!!(el.value)});
  } else if(t==='d2l-htmleditor'){
    out.editors.push({label:a(el,'label'),class:a(el,'class'),host:host(el),visible:vis(el)});
  } else if(t==='button'||/^d2l-button/.test(t)||a(el,'role')==='button'||t==='d2l-menu-item'){
    const txt=short(a(el,'text')||el.textContent);
    if(!txt&&!a(el,'aria-label'))continue;
    out.buttons.push({tag:t,text:txt,aria_label:a(el,'aria-label'),host:host(el),
      visible:vis(el),disabled:el.hasAttribute&&el.hasAttribute('disabled')});
  } else if(a(el,'role')==='radiogroup'){
    const r=el.getRootNode(); const id=a(el,'aria-labelledby');
    const nm=(r&&r.getElementById&&id&&r.getElementById(id))?short(r.getElementById(id).textContent):'';
    const levels=[...deep(el)].filter(x=>a(x,'role')==='radio').map(x=>short((x.textContent||'').split(/[,:]/)[0]));
    out.rubric.push({criterion:nm,levels:levels,host:host(el),visible:vis(el)});
  } else if(t==='select'){
    const opts=[...el.querySelectorAll('option')].map(o=>short(o.textContent)).slice(0,10);
    out.selects.push({aria_label:a(el,'aria-label'),options:opts});
  }
  if(/rubric/.test(t)){const k=t+(vis(el)?':visible':':hidden');out.rubric_tags[k]=(out.rubric_tags[k]||0)+1;}
  if(/^attach$/i.test(a(el,'aria-label').trim()))out.attach.push({tag:t,visible:vis(el)});
  if(t==='d2l-dialog'||t==='d2l-dialog-confirm'||a(el,'role')==='dialog')
    out.dialogs.push({tag:t,title:a(el,'title'),opened:el.hasAttribute&&el.hasAttribute('opened')});
}
out.url_path=location.pathname; out.title=document.title;
return out;
"""


def _scrub(obj, names: list[str]):
    """Replace every known learner name (either order) in all strings with [learner]."""
    pats = []
    for n in names:
        parts = [p for p in re.split(r"[\s,]+", n or "") if len(p) > 1]
        pats += [re.escape(n)] + [re.escape(p) for p in parts]
    rx = re.compile("|".join(sorted(set(pats), key=len, reverse=True)), re.I) if pats else None

    def walk(x):
        if isinstance(x, str):
            return rx.sub("[learner]", x) if rx else x
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        return x
    return walk(obj)


def _poll(fn, timeout: float = 15.0, every: float = 0.5) -> bool:
    """Poll ``fn`` until it returns truthy or ``timeout`` elapses (lazy-loaded Lit UI)."""
    end = time.time() + timeout
    while time.time() < end:
        try:
            if fn():
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(every)
    return False


_HAS_RUBRIC_JS = r"""
function* deep(root){
  const s=[root.documentElement||root];
  while(s.length){
    const n=s.pop(); if(!n)continue; yield n;
    if(n.shadowRoot)s.push(n.shadowRoot);
    for(const c of (n.children||[]))s.push(c);
  }
}
for(const el of deep(document)){
  if(el.getAttribute&&el.getAttribute('role')==='radiogroup')return true;
}
return false;
"""


def _product_checks(driver) -> dict:
    """What the shipping locators decide on the current page (read-only).

    Waits for each lazy-loaded control (write targets, Attach, rubric) before the
    locators run, so a miss means "not on the page", not "not rendered yet".
    """
    targets = wb._wait_for_write_targets(driver, timeout=20, require_feedback=True)
    _poll(lambda: wb._locate_attach_control(driver), timeout=10)
    rubric_seen = _poll(lambda: driver.execute_script(_HAS_RUBRIC_JS), timeout=15)
    chosen = None
    try:
        el = driver.execute_script(wb._FIND_SCORE_INPUT_JS, list(wb.SCORE_INPUT_SELECTORS))
        if el is not None:
            chosen = el.get_attribute("aria-label") or el.get_attribute("name") or "(no label)"
    except Exception as e:  # noqa: BLE001
        chosen = f"error: {e}"
    return {
        "targets_after_wait": targets,
        "rubric_radiogroups_seen": rubric_seen,
        "score_input_chosen_aria_label": chosen,
        "save_draft_found": wb._locate_save_draft(driver),
        "attach_control_found": wb._locate_attach_control(driver),
    }


def _inventory(driver) -> dict:
    try:
        return driver.execute_script(_INVENTORY_JS) or {}
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)}


# Everything visible inside the "Add Feedback Files" dialog frame: controls and short
# headings/messages only (no values, no file names).
_DIALOG_INVENTORY_JS = r"""
function vis(el){try{const r=el.getBoundingClientRect();return r.width>0&&r.height>0;}catch(e){return false;}}
function short(t){return (t||'').replace(/\s+/g,' ').trim().slice(0,100);}
const out={controls:[],text:[]};
const SEL=['button','a','input','select','label','h1','h2','h3','h4','[role=alert]',
  '[role=status]','.vui-heading-1','.vui-heading-2','.d2l-msg-container-text'].join(',');
for(const el of document.querySelectorAll(SEL)){
  const t=el.tagName.toLowerCase();
  const rec={tag:t,type:el.getAttribute('type')||'',text:short(el.textContent||el.value||el.getAttribute('aria-label')),
            visible:vis(el),disabled:!!el.disabled,checked:el.checked===undefined?null:!!el.checked};
  if(['button','a','input','select'].includes(t))out.controls.push(rec);else out.text.push(rec);
}
return out;
"""


def _inspect_import_dialog(driver) -> dict:
    """Open "Add Feedback Files", inventory the dialog frame, then Cancel. Uploads nothing."""
    from selenium.webdriver.common.by import By
    res: dict = {"opened": False}
    try:
        res["clicked"] = bool(driver.execute_script(wb._CLICK_ADD_FEEDBACK_FILES_JS))
        time.sleep(3)
        driver.switch_to.default_content()
        frames = driver.find_elements(By.TAG_NAME, "iframe")
        idx = [i for i, f in enumerate(frames)
               if (f.get_attribute("title") or "") == "Add Feedback Files"]
        res["dialog_frames"] = len(idx)
        if idx:
            driver.switch_to.frame(frames[idx[-1]])
            res["opened"] = True
            res["inventory"] = driver.execute_script(_DIALOG_INVENTORY_JS)
            res["cancel_clicked"] = bool(driver.execute_script(wb._CLICK_TEXT_EXACT_JS, "Cancel"))
            time.sleep(1)
    except Exception as e:  # noqa: BLE001
        res["error"] = str(e)
    finally:
        try:
            driver.switch_to.default_content()
        except Exception:  # noqa: BLE001
            pass
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True, help="BrightSpace assignment or quiz URL")
    ap.add_argument("--import-dialog", action="store_true",
                    help="Assignment route: also open the Add Feedback Files dialog, "
                         "inventory it and Cancel (uploads nothing)")
    ap.add_argument("--learners", type=int, default=2,
                    help="How many evaluation pages to inspect (default 2)")
    args = ap.parse_args()

    from cqc_cpcc.utilities.brightspace_fetch import (
        _open_and_login, _set_max_results_per_page,
    )
    from cqc_cpcc.utilities.selenium_util import get_session_driver, wait_for_ajax

    def progress(msg: str) -> None:
        print(f"  > {msg}", flush=True)

    route = detect_route(args.url)
    report: dict = {"route": route, "generated": _dt.datetime.now().isoformat(timespec="seconds"),
                    "score_selectors": list(wb.SCORE_INPUT_SELECTORS),
                    "feedback_selectors": list(wb.FEEDBACK_EDITOR_SELECTORS), "pages": []}
    names: list[str] = []
    driver, wait = get_session_driver()
    try:
        if route == ROUTE_QUIZ:
            from cqc_cpcc.utilities.brightspace_fetch import (
                _gather_quiz_attempts, _keep_last_attempt_per_user, _open_quiz_attempt,
                derive_quiz_grading_url,
            )
            grading_url = derive_quiz_grading_url(args.url)
            _open_and_login(driver, wait, grading_url, progress, None)
            _set_max_results_per_page(driver, wait, progress)
            learners = _keep_last_attempt_per_user(_gather_quiz_attempts(driver))
            names = [lr.get("name", "") for lr in learners]
            report["learners_found"] = len(learners)
            for i, lr in enumerate(learners[: args.learners], 1):
                page = {"learner": f"learner_{i}"}
                page["opened"] = _open_quiz_attempt(driver, wait, grading_url, lr)
                if page["opened"]:
                    page["attempt_view"] = {"checks": _product_checks(driver),
                                            "inventory": _inventory(driver)}
                    if wb._switch_quiz_view(driver, "completion summary"):
                        time.sleep(2)
                        page["completion_summary"] = {"checks": _product_checks(driver),
                                                      "inventory": _inventory(driver)}
                        wb._switch_quiz_view(driver, "attempt")
                report["pages"].append(page)
                progress(f"inspected learner_{i}")
        else:
            _open_and_login(driver, wait, args.url, progress, None)
            report["landing_url_path"] = driver.current_url.split("?")[0]
            users_url = wb._submissions_users_url(args.url)
            if "folder_submissions_users" not in (driver.current_url or "").lower():
                driver.get(users_url)
                wait_for_ajax(driver)
            report["users_view_url_path"] = driver.current_url.split("?")[0]
            report["add_feedback_files_found_on_users_view"] = _poll(
                lambda: wb._locate_add_feedback_files_control(driver), timeout=20)
            inv = _inventory(driver)
            # Page-level controls only: keep buttons that look like actions, drop row-level ones.
            keep = re.compile(r"feedback|download|publish|add|import|upload|more|actions|"
                              r"evaluat|rubric|email|filter|search|retract|save", re.I)
            report["users_view_buttons"] = sorted({
                (b.get("text") or b.get("aria_label") or "") + (" [visible]" if b.get("visible") else " [hidden]")
                for b in inv.get("buttons", [])
                if keep.search(b.get("text") or b.get("aria_label") or "")})
            if args.import_dialog:
                report["import_dialog"] = _inspect_import_dialog(driver)
            _set_max_results_per_page(driver, wait, progress)
            learners = wb._gather_assignment_learners(driver, users_url)
            names = [lr.get("name", "") for lr in learners]
            report["learners_found"] = len(learners)
            for i, lr in enumerate(learners[: args.learners], 1):
                page = {"learner": f"learner_{i}"}
                page["opened"] = wb._open_assignment_evaluation(driver, wait, args.url, lr)
                if page["opened"]:
                    page["checks"] = _product_checks(driver)
                    page["inventory"] = _inventory(driver)
                    # Existing feedback attachments show a "Remove Attachment: <file>" control.
                    page["feedback_attachment_count"] = sum(
                        1 for b in page["inventory"].get("buttons", [])
                        if (b.get("aria_label") or "").startswith("Remove Attachment"))
                report["pages"].append(page)
                progress(f"inspected learner_{i}")
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass

    report = _scrub(report, names)
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.abspath(os.path.join(
        OUT_DIR, f"probe-{route}-{_dt.datetime.now():%Y%m%d-%H%M%S}.json"))
    with open(path, "w") as fh:
        json.dump(report, fh, indent=2)
    print(f"\nReport: {path}")
    for p in report["pages"]:
        c = p.get("checks") or (p.get("attempt_view") or {}).get("checks")
        print(f"  {p['learner']}: opened={p['opened']} checks={c}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
