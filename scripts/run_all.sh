#!/usr/bin/env bash
# Run every check and regenerate the test evidence. Usage: bash scripts/run_all.sh
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8000}"

[ -f .env ] || { echo "Missing .env - copy .env.example to .env and add your API key"; exit 1; }
[ -d .venv ] && source .venv/bin/activate

echo "== 1/5 pytest (offline tests + real vision tests if a key is set)"
pytest -q

echo "== 2/5 test images"
python scripts/make_test_images.py

echo "== 3/5 start server on :$PORT"
# test runs use their own booking database so real requests are not mixed with test data
BOOKINGS_DB="$PWD/data/test_run_bookings.db" uvicorn app.main:app --port "$PORT" > server.log 2>&1 &
SERVER_PID=$!
trap 'kill $SERVER_PID 2>/dev/null || true' EXIT
for _ in $(seq 1 120); do curl -sf "http://localhost:$PORT/health" >/dev/null && break; sleep 1; done
curl -s "http://localhost:$PORT/health"; echo

echo "== 4/5 assignment test cases (A: 10 text, B: 5 images, C: 5 safety)"
python scripts/run_eval.py --base-url "http://localhost:$PORT"

echo "== 5/5 screenshots"
if python -c "import playwright" 2>/dev/null; then
  python scripts/capture_ui.py --base-url "http://localhost:$PORT" \
    --question "สุนัขหนัก 12 กิโล อาบน้ำราคาเท่าไหร่" --question "ร้านเปิดกี่โมง"
else
  echo "skipped (pip install playwright && playwright install chromium)"
fi
echo "Done. Results: docs/test_results.md, docs/test_results.csv, docs/screenshots/"
