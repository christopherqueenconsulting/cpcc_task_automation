#!/usr/bin/env python
#  Copyright (c) 2026. Christopher Queen Consulting LLC (http://www.ChristopherQueenConsulting.com/)

"""Diagnose a broken CPCC / BrightSpace login: changed DOM, bad credentials, MFA, or infra.

Drives the SSO flow one step at a time with GENERIC locators (so it can keep going
when the product's locators no longer match) and, at every step, records:

- the page (host + path only, title, readyState) and a screenshot;
- whether each locator the product's ``login_if_needed`` / ``microsoft_login`` /
  ``duo_login`` uses (``src/cqc_cpcc/utilities/utils.py``) still matches the page;
- the visible inputs, buttons and error messages, so a renamed id or class is obvious.

It then prints a verdict: INFRA, CREDENTIALS, MFA, DOM_CHANGED, or LOGIN_OK. The
verdict is a hint, read the step table too. The password is never printed, logged or
written: values typed into fields are not recorded and the JSON report is scrubbed of the
user id and password. Reports go to ``output/login_probe/`` (gitignored, mode 0700).

Screenshots are NOT scrubbed. A Microsoft sign-in screenshot can show the account email,
tile and MFA number, so never share or attach that folder. Once the browser is on an
authenticated BrightSpace page the probe records host and path only: no screenshot, title
or page inventory, because those pages can hold student names and grades.

``--fresh`` and ``--product`` delete the Microsoft and BrightSpace cookies in the app's
persistent browser profile, which signs the app out. Undo: sign in once more and approve
the MFA number (the probe's own login does this and clicks "Yes" on "Stay signed in?").

Credentials, first found wins: ``INSTRUCTOR_USERID`` / ``INSTRUCTOR_PASS`` in the
environment or ``.env``; ``--user`` / ``--password-file``; otherwise a prompt on a TTY.

    HEADLESS_BROWSER=false BROWSER_TYPE=DOCKER_CHROME DOCKER_TYPE=LOCAL \\
      poetry run python scripts/login_probe.py
    poetry run python scripts/login_probe.py --product     # also run the product's own login
    poetry run python scripts/login_probe.py --fresh       # drop cookies first (forces a login)

Watch the browser at http://localhost:17900 (its password is in docs/SELENIUM_MCP.md). Only one client can use
the Docker grid at a time (maxSessions 1): close the Selenium MCP browser first.
"""

import argparse
import datetime as _dt
import getpass
import json
import os
import re
import sys
import time
from urllib.parse import urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "output", "login_probe")
DEFAULT_URL = "https://brightspace.cpcc.edu/d2l/home"

# Every locator the product's login code relies on, copied from utils.py. A "miss" on a
# page where the step is active is the DOM-changed signal.
PRODUCT_LOCATORS = {
    "ms.loginHeader": ("xpath", "//div[@id='loginHeader']"),
    "ms.tile_holder": ("xpath", "//*[@id='tilesHolder']"),
    "ms.displayName": ("xpath", "//div[@id='displayName']"),
    "ms.use_another": ("xpath", "//div[contains(text(),'Use another')]/parent::div"),
    "ms.back_button": ("xpath", "//button[@class='backButton']"),
    "ms.username(loginfmt)": ("xpath", "//input[@name='loginfmt']"),
    "ms.next_button": ("xpath", "//input[contains(@class, 'button_primary') and contains(@value,'Next')]"),
    "ms.password(passwd)": ("xpath", "//input[@name='passwd']"),
    "ms.signin_button": ("xpath", "//input[contains(@class, 'button_primary') and contains(@value,'Sign in')]"),
    "ms.number_match": ("xpath", "//*[@id='idRichContext_DisplaySign']"),
    "ms.kmsi_yes": ("xpath", "//input[@id='idSIButton9'] | //input[@type='submit' and contains(@value,'Yes')] | "
                             "//input[contains(@class, 'button_primary') and contains(@value,'Yes')]"),
    "duo.banner": ("xpath", "//div[@class='sr-only' and contains(text(),'Login')]"),
    "duo.username": ("xpath", "//*[@id='username']"),
    "duo.password": ("xpath", "//*[@id='password']"),
    "duo.proceed": ("xpath", "//*[@name='_eventId_proceed']"),
}
PRODUCT_TITLES = {"Web Login Service": "duo_login", "Sign in to your account": "microsoft_login"}

# Page inventory: visible inputs/buttons/links-as-buttons and error-looking text. No values.
_INVENTORY_JS = r"""
// Microsoft keeps the inactive pane (e.g. the password input) in the DOM, off-screen or
// transparent, so a bare size check says "visible". Check opacity, ancestors and viewport too.
function vis(el){try{
  if(el.checkVisibility&&!el.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}))return false;
  const r=el.getBoundingClientRect();
  if(r.width<=0||r.height<=0)return false;
  if(r.right<=0||r.bottom<=0||r.left>=innerWidth||r.top>=innerHeight)return false;
  for(let p=el;p&&p.nodeType===1;p=p.parentElement){
    const s=getComputedStyle(p);
    if(s.visibility==='hidden'||s.display==='none'||parseFloat(s.opacity)===0)return false;
  }
  return true;}catch(e){return false;}}
function short(t){return (t||'').replace(/\s+/g,' ').trim().slice(0,120);}
const out={inputs:[],buttons:[],errors:[],headings:[],ids_with_error:[]};
for(const el of document.querySelectorAll('input,textarea,select')){
  const t=(el.getAttribute('type')||el.tagName).toLowerCase();
  if(t==='hidden')continue;
  if(t==='submit'||t==='button'){
    out.buttons.push({tag:'input',id:el.id,cls:short(el.className),text:short(el.value),visible:vis(el)});
  }else{
    out.inputs.push({tag:el.tagName.toLowerCase(),type:t,id:el.id,name:el.name||'',
      aria:short(el.getAttribute('aria-label')),visible:vis(el)});
  }
}
for(const el of document.querySelectorAll('button,[role=button]')){
  out.buttons.push({tag:el.tagName.toLowerCase(),id:el.id,cls:short(el.className),
    text:short(el.textContent||el.getAttribute('aria-label')),visible:vis(el)});
}
for(const el of document.querySelectorAll('h1,h2,[role=heading],#loginHeader,#idDiv_SAOTCAS_Title,#idRichContext_DisplaySign')){
  if(vis(el))out.headings.push(short(el.textContent));
}
for(const el of document.querySelectorAll('[role=alert],.error,.alert-error,[id*=Error],[id*=error]')){
  if(vis(el)&&short(el.textContent))out.errors.push({id:el.id,text:short(el.textContent)});
}
return out;
"""

_ERROR_PATTERNS = {
    "bad_password": re.compile(r"password is incorrect|incorrect password|invalid (user|password|credentials)|"
                               r"wrong password|login failed", re.I),
    "unknown_account": re.compile(r"doesn.?t exist|couldn.?t find an account|no account|unknown (user|account)", re.I),
    "locked": re.compile(r"locked|too many (failed )?(attempts|requests)|temporarily", re.I),
    "mfa_denied": re.compile(r"denied|declined|didn.?t approve|request (was )?(denied|timed out)", re.I),
}


def _by(kind: str):
    from selenium.webdriver.common.by import By
    return {"xpath": By.XPATH, "name": By.NAME, "id": By.ID, "css": By.CSS_SELECTOR}[kind]


class Probe:
    def __init__(self, driver, secrets: list[str]):
        self.driver = driver
        self.steps: list[dict] = []
        self.secrets = [s for s in secrets if s]
        os.makedirs(OUT_DIR, mode=0o700, exist_ok=True)
        self.stamp = f"{_dt.datetime.now():%Y%m%d-%H%M%S}"

    def scrub(self, obj):
        def walk(x):
            if isinstance(x, str):
                for s in self.secrets:
                    x = re.sub(re.escape(s), "[redacted]", x, flags=re.I)
                return x
            if isinstance(x, list):
                return [walk(v) for v in x]
            if isinstance(x, dict):
                return {k: walk(v) for k, v in x.items()}
            return x
        return walk(obj)

    def snapshot(self, label: str) -> dict:
        d = self.driver
        step: dict = {"n": len(self.steps) + 1, "label": label, "t": time.strftime("%H:%M:%S")}
        try:
            u = urlparse(d.current_url or "")
            step["host"] = u.hostname or ""
            step["where"] = f"{u.hostname}{u.path}"
            if _on_brightspace(d.current_url or "") and "login" not in u.path.lower():
                # Authenticated BrightSpace page: it can hold student names and grades.
                step["note"] = "authenticated BrightSpace page: host and path only"
                self.steps.append(step)
                return step
            step["title"] = d.title
            step["ready"] = d.execute_script("return document.readyState")
        except Exception as e:  # noqa: BLE001
            step["error"] = f"page read failed: {e}"
            self.steps.append(step)
            return step
        step["product_title_route"] = PRODUCT_TITLES.get(step["title"], "no match (login_if_needed would do nothing)")
        step["product_locators"] = {}
        for name, (kind, value) in PRODUCT_LOCATORS.items():
            try:
                step["product_locators"][name] = len(d.find_elements(_by(kind), value))
            except Exception:  # noqa: BLE001
                step["product_locators"][name] = -1
        try:
            step["inventory"] = d.execute_script(_INVENTORY_JS)
        except Exception as e:  # noqa: BLE001
            step["inventory"] = {"error": str(e)}
        shot = os.path.abspath(os.path.join(OUT_DIR, f"{self.stamp}-step{step['n']:02d}.png"))
        try:
            d.save_screenshot(shot)
            step["screenshot"] = shot
        except Exception as e:  # noqa: BLE001
            step["screenshot"] = f"failed: {e}"
        self.steps.append(step)
        return step

    # -- tiny helpers that use GENERIC locators, never the product's -------------------
    def first_visible(self, css_list: list[str]):
        for css in css_list:
            for el in self.driver.find_elements(*(_by("css"), css)):
                try:
                    if el.is_displayed() and el.is_enabled():
                        return el
                except Exception:  # noqa: BLE001
                    continue
        return None

    def type_into(self, el, text: str) -> None:
        el.click()
        el.clear()
        el.send_keys(text)

    def click_primary(self) -> bool:
        el = self.first_visible(["#idSIButton9", "input[type=submit]", "button[type=submit]"])
        if el is None:
            return False
        el.click()
        return True


def _on_brightspace(url: str) -> bool:
    host = urlparse(url or "").hostname or ""
    return host == "brightspace.cpcc.edu" or host.endswith(".brightspace.cpcc.edu")


def _classify(step: dict) -> str:
    where = step.get("where", "")
    host = step.get("host", "")
    inv = step.get("inventory", {}) or {}
    pl = step.get("product_locators", {})
    # Visible only: Microsoft leaves a hidden loginfmt input on the password page.
    vis_inputs = [i for i in inv.get("inputs", []) if i.get("visible")]
    ids = {i.get("id") for i in vis_inputs} | {i.get("id") for i in inv.get("buttons", []) if i.get("visible")}
    names = {i.get("name") for i in vis_inputs}
    if _on_brightspace(f"https://{where}") and "login" not in where:
        return "brightspace"
    if pl.get("ms.number_match") or "idRichContext_DisplaySign" in " ".join(inv.get("headings", [])):
        return "mfa_number"
    if "passwd" in names:
        return "ms_password"
    if "loginfmt" in names:
        return "ms_username"
    if "idSIButton9" in ids and any("stay signed in" in h.lower() for h in inv.get("headings", [])):
        return "kmsi"
    if pl.get("ms.tile_holder") or pl.get("ms.displayName") or host == "login.microsoftonline.com":
        return "ms_other"
    if "username" in ids and "password" in ids:
        return "duo_form"
    return "unknown"


def _error_kind(step: dict) -> tuple[str | None, str]:
    texts = [e.get("text", "") for e in (step.get("inventory", {}) or {}).get("errors", [])]
    blob = " | ".join(texts)
    for kind, rx in _ERROR_PATTERNS.items():
        if rx.search(blob):
            return kind, blob
    return None, blob


def run_probe(probe: Probe, url: str, user: str, password: str, max_seconds: int) -> dict:
    d = probe.driver
    d.get(url)
    time.sleep(3)
    result: dict = {"outcome": None, "notes": []}
    submitted_user = submitted_pw = False
    deadline = time.time() + max_seconds
    last_state = None
    while time.time() < deadline:
        step = probe.snapshot("loop")
        state = _classify(step)
        step["state"] = state
        kind, blob = _error_kind(step)
        if kind:
            result.update(outcome="CREDENTIALS" if kind in {"bad_password", "unknown_account", "locked"} else "MFA",
                          notes=[f"Microsoft shows an error: {kind}: {blob}"])
            return result
        if state == "brightspace":
            result["outcome"] = "LOGIN_OK"
            return result
        print(f"  step {step['n']}: {state:12s} {step.get('where')}  title={step.get('title')!r}", flush=True)
        if state == "ms_username" and not submitted_user:
            el = probe.first_visible(["input[name=loginfmt]", "input[type=email]"])
            if el is None:
                result.update(outcome="DOM_CHANGED", notes=["username page seen but no visible username input"])
                return result
            probe.type_into(el, user if "@" in user else f"{user}@cpcc.edu")
            submitted_user = probe.click_primary() or True
            time.sleep(2)
        elif state == "ms_password" and not submitted_pw:
            el = probe.first_visible(["input[name=passwd]", "input[type=password]"])
            if el is None:
                result.update(outcome="DOM_CHANGED", notes=["password page seen but no visible password input"])
                return result
            probe.type_into(el, password)
            submitted_pw = probe.click_primary() or True
            time.sleep(3)
        elif state == "ms_password" and submitted_pw and last_state == "ms_password":
            time.sleep(2)  # password page again after submit: wait for an error to render
        elif state == "ms_other":
            tile = probe.first_visible(["#tilesHolder .tile", "#tilesHolder [role=button]", "#tilesHolder div"])
            more = probe.first_visible(["#otherTile", "[data-test-id=otherTile]"])
            pick = None
            for el in d.find_elements(*(_by("css"), "#tilesHolder *")):
                if user.lower() in (el.text or "").lower():
                    pick = el
                    break
            target = pick or more or tile
            if target is None:
                result.update(outcome="DOM_CHANGED", notes=["unrecognized Microsoft page, no account tile or username box"])
                return result
            target.click()
            time.sleep(2)
        elif state == "mfa_number":
            num = ""
            try:
                num = d.find_element(*(_by("xpath"), PRODUCT_LOCATORS["ms.number_match"][1])).text.strip()
            except Exception:  # noqa: BLE001
                pass
            print(f"\n  >>> MFA: enter {num or '(number not readable)'} in Microsoft Authenticator and approve. "
                  f"Waiting up to 120 s...\n", flush=True)
            end = time.time() + 120
            while time.time() < end and _classify(probe.snapshot("mfa-wait")) == "mfa_number":
                time.sleep(3)
            if _classify(probe.steps[-1]) == "mfa_number":
                result.update(outcome="MFA", notes=["number-matching prompt not approved within 120 s"])
                return result
        elif state == "kmsi":
            probe.click_primary()
            time.sleep(3)
        elif state == "duo_form":
            u_el = probe.first_visible(["#username", "input[name=username]"])
            p_el = probe.first_visible(["#password", "input[name=password]"])
            if u_el is None or p_el is None:
                result.update(outcome="DOM_CHANGED", notes=["Duo form recognized but inputs not usable"])
                return result
            probe.type_into(u_el, user)
            probe.type_into(p_el, password)
            btn = probe.first_visible(["[name=_eventId_proceed]", "button[type=submit]", "input[type=submit]"])
            if btn:
                btn.click()
            time.sleep(4)
        elif state == "unknown":
            result.update(outcome="DOM_CHANGED", notes=["page is neither BrightSpace nor any login step the product knows"])
            return result
        else:
            time.sleep(2)
        last_state = state
    result.update(outcome="TIMEOUT", notes=[f"no result after {max_seconds}s; last state {last_state}"])
    return result


def product_login_check(driver, url: str) -> dict:
    """Run the shipping ``login_if_needed`` and report what it did. Credentials come from env."""
    from cqc_cpcc.utilities.utils import login_if_needed
    out: dict = {}
    try:
        driver.get(url)
        time.sleep(2)
        out["title_before"] = driver.title
        login_if_needed(driver)
        time.sleep(3)
        out["success"] = _on_brightspace(driver.current_url)
        u = urlparse(driver.current_url or "")
        out["landed"] = f"{u.hostname}{u.path}"
    except Exception as e:  # noqa: BLE001
        out["success"] = False
        out["exception"] = f"{type(e).__name__}: {str(e).splitlines()[0][:300] if str(e) else ''}"
    return out


def verdict(result: dict, steps: list[dict], product: dict | None) -> list[str]:
    lines = [f"Outcome: {result['outcome']}"] + [f"  {n}" for n in result.get("notes", [])]
    # Product locators that never matched on a page where that step was active.
    seen = {}
    state_needs = {
        "ms_username": ["ms.username(loginfmt)", "ms.next_button"],
        "ms_password": ["ms.password(passwd)", "ms.signin_button"],
        "mfa_number": ["ms.number_match"],
        "kmsi": ["ms.kmsi_yes"],
        "duo_form": ["duo.username", "duo.password", "duo.proceed"],
    }
    for s in steps:
        for loc in state_needs.get(s.get("state", ""), []):
            seen[loc] = max(seen.get(loc, 0), s.get("product_locators", {}).get(loc, 0))
        if s.get("state", "").startswith("ms_") and s.get("title") != "Sign in to your account":
            lines.append(f"  step {s['n']}: Microsoft page title is {s.get('title')!r}, product expects "
                         f"'Sign in to your account', so login_if_needed would skip login")
            result["outcome"] = result["outcome"] if result["outcome"] != "LOGIN_OK" else "DOM_CHANGED"
    misses = [k for k, v in seen.items() if v == 0]
    if misses:
        lines.append(f"  Product locators that matched nothing while their step was on screen: {', '.join(misses)}")
        lines.append("  => DOM_CHANGED: update those locators in src/cqc_cpcc/utilities/utils.py "
                     "(compare each step's inventory in the JSON report).")
    if product is not None:
        lines.append(f"Product login_if_needed: success={product.get('success')} "
                     + (f"exception={product['exception']}" if product.get("exception") else f"landed={product.get('landed')}"))
        if result["outcome"] == "LOGIN_OK" and not product.get("success"):
            lines.append("  => Generic login works, product login fails: the code, not credentials/MFA/infra.")
    meaning = {
        "LOGIN_OK": "Credentials, MFA and the SSO pages all work. If the app still fails, the login code is not the cause "
                    "(check env var names, the profile/cookie state, or post-login navigation).",
        "CREDENTIALS": "Microsoft rejected the account. Not a code or DOM problem: fix INSTRUCTOR_USERID / INSTRUCTOR_PASS.",
        "MFA": "Login reached MFA but it was not approved or was refused. Not a DOM problem.",
        "DOM_CHANGED": "The SSO pages differ from what the code expects. See the step inventories and screenshots.",
        "TIMEOUT": "Probe did not reach BrightSpace in time. Read the last screenshot.",
    }
    lines.append(meaning.get(result["outcome"], ""))
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default=DEFAULT_URL, help="BrightSpace page to open (default: home)")
    ap.add_argument("--user", help="Instructor user id (default INSTRUCTOR_USERID, else prompt)")
    ap.add_argument("--password-file", help="File whose first line is the password (default INSTRUCTOR_PASS, else prompt)")
    ap.add_argument("--fresh", action="store_true", help="Delete the Microsoft and BrightSpace cookies in the app's browser profile first, to force a login "
                         "(signs the app out; the next app run needs one MFA approval)")
    ap.add_argument("--product", action="store_true", help="Afterwards run the product's own login_if_needed on a fresh login. Always deletes the Microsoft and "
                         "BrightSpace cookies first (signs the app out; the next app run needs one MFA approval)")
    ap.add_argument("--max-seconds", type=int, default=180)
    args = ap.parse_args()

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:  # noqa: BLE001
        pass

    user = args.user or os.environ.get("INSTRUCTOR_USERID") or ""
    password = ""
    if args.password_file:
        with open(args.password_file) as fh:
            password = fh.readline().rstrip("\r\n")
    password = password or os.environ.get("INSTRUCTOR_PASS", "")
    if not user and sys.stdin.isatty():
        user = input("Instructor user id: ").strip()
    if not password and sys.stdin.isatty():
        password = getpass.getpass("Instructor password (hidden): ")
    print(f"Credentials: user id {'set' if user else 'MISSING'}, password {'set' if password else 'MISSING'}")
    if not (user and password):
        print("Missing credentials. Set INSTRUCTOR_USERID/INSTRUCTOR_PASS, or use --user and --password-file.")
        return 2
    os.environ["INSTRUCTOR_USERID"], os.environ["INSTRUCTOR_PASS"] = user, password

    report: dict = {"generated": _dt.datetime.now().isoformat(timespec="seconds"), "start_url": args.url}
    print("Starting browser (same driver the app uses)...", flush=True)
    try:
        from cqc_cpcc.utilities.selenium_util import get_session_driver
        driver, _wait = get_session_driver()
    except Exception as e:  # noqa: BLE001
        print(f"\nOutcome: INFRA\n  Could not start the browser: {type(e).__name__}: {str(e).splitlines()[0][:300]}")
        print("  Not a login/DOM problem. Check: docker compose -p cpcc_task_automation -f docker-compose.yml "
              "up -d selenium-chrome ; curl http://localhost:14444/wd/hub/status ; close other grid clients.")
        return 1

    probe = Probe(driver, [user, password])
    product = None
    try:
        if args.fresh or args.product:
            print("Note: clearing Microsoft and BrightSpace cookies in the app's browser profile; "
                  "the next app run needs one MFA approval.", flush=True)
        if args.fresh:
            for u in ("https://login.microsoftonline.com", "https://brightspace.cpcc.edu"):
                try:
                    driver.get(u)
                    driver.delete_all_cookies()
                except Exception:  # noqa: BLE001
                    pass
        print("Probing login flow...", flush=True)
        try:
            result = run_probe(probe, args.url, user, password, args.max_seconds)
        except Exception as e:  # noqa: BLE001
            result = {"outcome": "INFRA", "notes": [f"{type(e).__name__}: {str(e).splitlines()[0][:300]}"]}
        if args.product:
            print("Running the product's login_if_needed...", flush=True)
            try:
                for u in ("https://login.microsoftonline.com", "https://brightspace.cpcc.edu"):
                    driver.get(u)
                    driver.delete_all_cookies()
            except Exception:  # noqa: BLE001
                pass
            product = product_login_check(driver, args.url)
    finally:
        try:
            driver.quit()
        except Exception:  # noqa: BLE001
            pass

    lines = verdict(result, probe.steps, product)
    report.update(result=result, product_login=product, steps=probe.steps, verdict=lines)
    path = os.path.abspath(os.path.join(OUT_DIR, f"login-probe-{probe.stamp}.json"))
    with open(path, "w") as fh:
        json.dump(probe.scrub(report), fh, indent=2)
    print("\n" + "\n".join(probe.scrub(lines)))
    print(f"\nReport: {path}\nScreenshots: {os.path.abspath(OUT_DIR)}")
    return 0 if result["outcome"] == "LOGIN_OK" else 1


if __name__ == "__main__":
    raise SystemExit(main())
