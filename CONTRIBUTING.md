# Contributing to Sentinel

Thanks for your interest! This is a portfolio / demo project, but contributions
are welcome.

## Development setup

```bash
# Clone and create a virtual environment
git clone https://github.com/HariHaran9597/Sentinel-Self-Healing-DevOps-Agent.git
cd Sentinel-Self-Healing-DevOps-Agent
python -m venv .venv
source .venv/bin/activate  # or .venv/Scripts/activate on Windows
pip install -r agent/requirements-dev.txt

# Run unit tests
pytest agent/tests -q

# Local development (no Docker)
LOG_PATH=./logs/app.log python demo-server/server.py           # terminal 1
LOG_PATH=./logs/app.log EXECUTOR_MODE=local python -m uvicorn app.main:app --app-dir agent  # terminal 2
```

## Adding a new runbook

1. Create `agent/runbooks/RB-NNN-descriptive-name.md` following the existing
   frontmatter schema (see `RB-001-db-conn-pool-exhausted.md` for the template).
2. Key fields: `error_codes` (array the monitor matches), `fix_action`
   (must be one of `restart_container`, `clean_logs_and_restart`, or `manual`),
   `fix_command`, and `risk`.
3. The ChromaDB collection is re-seeded automatically on the next agent startup
   when the runbook file content changes.

## Project structure

```
agent/app/          # Agent core: monitor, graph, nodes, executor, runbooks store
agent/runbooks/     # Markdown runbooks with YAML frontmatter
agent/tests/        # Unit tests
agent/static/       # Dashboard HTML
demo-server/         # Crashable demo service
scripts/             # End-to-end demo driver
docs/images/         # Screenshots
```

## Pull request process

1. Fork the repo and create a feature branch.
2. Ensure `pytest agent/tests -q` passes.
3. Open a pull request against `main` with a clear description.
