"""Run every check and regenerate the test evidence (Windows, macOS and Linux).

    python scripts/run_all.py

Steps: pytest -> start the server -> 20 assignment test cases -> screenshots (if
Playwright is installed) -> stop the server. Test runs use their own booking database
(data/test_run_bookings.db) so real booking requests are not mixed with test data.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.getenv("PORT", "8000"))
BASE = f"http://localhost:{PORT}"
PY = sys.executable


def step(title: str) -> None:
    print(f"\n== {title}", flush=True)


def run(args: list[str]) -> int:
    return subprocess.call([PY, *args], cwd=ROOT)


def wait_for_server(timeout_s: int = 180) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/health", timeout=3) as r:
                print(r.read().decode("utf-8"))
                return True
        except OSError:
            time.sleep(2)
    return False


def main() -> int:
    if not (ROOT / ".env").exists():
        print("Missing .env - copy .env.example to .env and add your API key")
        return 1

    step("1/4 pytest")
    if run(["-m", "pytest", "-q"]) != 0:
        print("pytest reported failures - fix them before collecting test evidence")
        return 1

    step(f"2/4 start server on {BASE} (first start may download the embedding model)")
    env = dict(os.environ, BOOKINGS_DB=str(ROOT / "data" / "test_run_bookings.db"))
    log = open(ROOT / "server.log", "w", encoding="utf-8")
    server = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--port", str(PORT)],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        if not wait_for_server():
            print("Server did not start - see server.log")
            return 1

        step("3/4 assignment test cases (A: 10 text, B: 5 images, C: 5 safety)")
        run(["scripts/run_eval.py", "--base-url", BASE])

        step("4/4 screenshots")
        try:
            import playwright  # noqa: F401
        except ImportError:
            print("skipped (pip install playwright, then: python -m playwright install chromium)")
        else:
            run(["scripts/capture_ui.py", "--base-url", BASE,
                 "--question", "สุนัขหนัก 12 กิโล อาบน้ำราคาเท่าไหร่", "--question", "ร้านเปิดกี่โมง"])
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()

    print("\nDone. Results: docs/test_results.md, docs/test_results.csv, docs/screenshots/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
