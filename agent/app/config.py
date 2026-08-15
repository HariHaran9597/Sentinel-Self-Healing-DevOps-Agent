"""Environment-driven settings. Defaults target local development; docker
compose overrides everything relevant via environment variables."""
from __future__ import annotations

import os
from pathlib import Path

AGENT_DIR = Path(__file__).resolve().parent.parent
PROJECT_DIR = AGENT_DIR.parent


def _env_path(name: str, default: Path) -> Path:
    return Path(os.environ.get(name) or default)


class Settings:
    def __init__(self) -> None:
        # Phase 1 -- the monitor
        self.log_path = _env_path("LOG_PATH", PROJECT_DIR / "logs" / "app.log")
        self.monitor_poll_seconds = float(os.environ.get("MONITOR_POLL_SECONDS", "0.5"))
        self.cooldown_seconds = float(os.environ.get("COOLDOWN_SECONDS", "30"))

        # Phase 2 -- the brain
        self.runbooks_dir = _env_path("RUNBOOKS_DIR", AGENT_DIR / "runbooks")
        self.chroma_path = _env_path("CHROMA_PATH", AGENT_DIR / "data" / "chroma")

        # Remediation target
        self.target_container = os.environ.get("TARGET_CONTAINER", "demo-server")
        self.demo_server_url = os.environ.get("DEMO_SERVER_URL", "http://127.0.0.1:8080")
        self.target_health_url = os.environ.get(
            "TARGET_HEALTH_URL", self.demo_server_url.rstrip("/") + "/health"
        )
        self.executor_mode = os.environ.get("EXECUTOR_MODE", "auto")  # auto|docker|local|dry-run
        self.verify_timeout_seconds = float(os.environ.get("VERIFY_TIMEOUT_SECONDS", "60"))

        # Phase 3 -- human in the loop (set false for fully autonomous operation)
        self.require_approval = os.environ.get("REQUIRE_APPROVAL", "true").lower() in {
            "1", "true", "yes", "on",
        }

        # Local (non-docker) remediation: relaunch the demo server as a subprocess
        self.local_server_script = _env_path("LOCAL_SERVER_SCRIPT", PROJECT_DIR / "demo-server" / "server.py")
        self.local_server_port = int(os.environ.get("LOCAL_SERVER_PORT", "8080"))

        # Optional LLM-enriched diagnosis (any OpenAI-compatible endpoint)
        self.openai_api_key = os.environ.get("OPENAI_API_KEY", "")
        self.openai_base_url = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.openai_model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
