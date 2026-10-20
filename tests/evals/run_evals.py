"""Deterministic phrase-class eval against a running API: .venv/bin/python tests/evals/run_evals.py [API_URL]

Pass: the expected tool was called (when one is expected), every "must say" class matched, no
"never" pattern matched, refusal cases refused and other cases did not. Live Gemini + Maps calls,
so run it sparingly (quota). Exit code 1 on any failure.
"""

import re
import sys
import time

import requests

from cases import CASES, COUNTY, NEVER

API = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080").rstrip("/")


def run_case(c: dict) -> tuple[bool, list[str], float]:
    t0 = time.time()
    r = requests.post(f"{API}/v1/chat", json={"county_fips": COUNTY, "message": c["q"]}, timeout=300)
    dt = time.time() - t0
    if r.status_code != 200:
        return False, [f"HTTP {r.status_code}: {r.text[:160]}"], dt
    body = r.json()
    text = " ".join(s["text"] for s in body["reply"])
    used = {t["name"] for t in body["tool_calls"]}
    problems = []
    if c["tools"] and not used & set(c["tools"]):
        problems.append(f"expected one of {c['tools']}, called {sorted(used) or 'none'}")
    for name, pat in c["must"].items():
        if not re.search(pat, text, re.IGNORECASE):
            problems.append(f"missing: {name}")
    for pat in NEVER:
        if re.search(pat, text, re.IGNORECASE):
            problems.append(f"said a never-say phrase: {pat}")
    if bool(c.get("refuse")) != body["refused"]:
        problems.append(f"refused={body['refused']}, expected {bool(c.get('refuse'))}")
    if body["refused"] and used:
        problems.append(f"refused but still called {sorted(used)}")
    return not problems, problems, dt


if __name__ == "__main__":
    only = set(sys.argv[2:])
    failed = 0
    for c in CASES:
        if only and c["id"] not in only:
            continue
        ok, problems, dt = run_case(c)
        failed += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {c['id']:<28} {dt:5.1f}s  {'; '.join(problems)}", flush=True)
    print(f"\n{len(CASES) - failed if not only else len(only) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
