#!/usr/bin/env bash
# ClaimShield (macOS / Linux).  Usage: ./run.sh [setup|app|offline|demo|doctor|check|test|build-kb|snapshot|samples|eval|credits|models]
set -e
cd "$(dirname "$0")"
cmd="${1:-app}"; shift || true
[ "$cmd" != "setup" ] && [ -f venv/bin/activate ] && source venv/bin/activate
case "$cmd" in
  setup)    python3 -m venv venv && source venv/bin/activate && pip install -q --upgrade pip && pip install -r requirements.txt
            [ -f .env ] || { cp .env.example .env; echo "Created .env — add API keys, or leave it empty for offline mode"; }
            python scripts/doctor.py || true; echo; echo "Ready. Start the app with: ./run.sh app" ;;
  samples)  python scripts/make_scenarios.py ;;
  app)      python -m streamlit run app/ui.py ;;
  offline)  LLM_MODE=off python -m streamlit run app/ui.py ;;
  demo)     LLM_MODE=replay python -m streamlit run app/ui.py ;;
  doctor)   python scripts/doctor.py ;;
  check)    python scripts/doctor.py --live ;;
  models)   python scripts/doctor.py --models "${1:-fireworks}" ;;
  test)     python -m pytest -q ;;
  build-kb) python scripts/build_kb.py "$@" ;;
  eval)     python eval/run_eval.py --engines rules lean single --max-usd 8 ;;
  snapshot) python scripts/demo.py snapshot "$@" ;;
  credits)  python scripts/demo.py credits ;;
  *)        echo "usage: ./run.sh [setup|app|offline|demo|doctor|check|test|build-kb|snapshot|samples|eval|credits|models]" ;;
esac
