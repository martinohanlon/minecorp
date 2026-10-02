"""Settings from the environment, with the project .env file as a fallback."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Load KEY=VALUE lines from .env into os.environ without overriding real env vars."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


@dataclass(frozen=True)
class Settings:
    """Runtime settings. The shared secret is required; the service refuses to start without it."""
    host: str
    port: int
    shared_secret: str
    openai_model: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_database: str
    max_turns: int          # model calls per question (tool rounds + the final answer)
    llm_timeout: float      # seconds per model call
    trace_dir: Path         # one JSON line per question is appended here


def get_settings() -> Settings:
    """Read settings after loading .env. Binds to localhost unless AGENT_HOST says otherwise."""
    load_dotenv()
    secret = os.environ.get("AGENT_SHARED_SECRET", "")
    if len(secret) < 16:
        raise SystemExit("AGENT_SHARED_SECRET must be set (16+ characters), e.g. in .env.")
    return Settings(
        host=os.environ.get("AGENT_HOST", "127.0.0.1"),
        port=int(os.environ.get("AGENT_PORT", "8080")),
        shared_secret=secret,
        openai_model=os.environ.get("OPENAI_MODEL", "gpt-5.4-mini"),
        neo4j_uri=os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        neo4j_user=os.environ.get("NEO4J_USER", "neo4j"),
        neo4j_password=os.environ.get("NEO4J_PASSWORD", ""),
        neo4j_database=os.environ.get("NEO4J_DATABASE", "neo4j"),
        max_turns=int(os.environ.get("AGENT_MAX_TURNS", "6")),
        llm_timeout=float(os.environ.get("AGENT_LLM_TIMEOUT", "40")),
        trace_dir=Path(os.environ.get("AGENT_TRACE_DIR", str(ROOT / "agent" / "logs"))),
    )
