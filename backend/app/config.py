"""Two config sources, on purpose:

- `settings` (environment / .env): where services live, keys and model names.
  Changes per deployment. Secrets live here.
- `cfg` (config.yaml): how the harness behaves (limits, retries, timeouts).
  Versioned with the code. Unknown keys fail at startup, so a typo never
  silently falls back to a default.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    # Chat LLM. "fake" runs a rule-based planner: no key, no model server.
    llm_default: Literal["fake", "openai"] = "fake"
    llm_base_url: str = "http://localhost:1234/v1"
    llm_api_key: str = "lm-studio"
    llm_model: str = "qwen/qwen3.5-9b"

    # Embeddings for the knowledge base. Empty base URL / key = reuse LLM_*.
    embed_base_url: str = ""
    embed_api_key: str = ""
    embed_model: str = "text-embedding-bge-m3"

    # RAGAS judge. Empty = reuse LLM_*.
    judge_base_url: str = ""
    judge_api_key: str = ""
    judge_model: str = ""
    judge_json_mode: str = "json_schema"  # instructor mode: json_schema | json | md_json | tools

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    db_path: Path = ROOT / "data" / "harness.db"
    log_level: str = "INFO"
    log_format: Literal["json", "text"] = "json"
    allow_fault_injection: bool = True
    approver_token: str = ""  # set = approvals need the X-Approver-Token header
    cors_origins: str = "http://localhost:4200"

    data_dir: Path = ROOT / "data"
    config_path: Path = ROOT / "config.yaml"

    @field_validator("db_path", "data_dir", "config_path")
    @classmethod
    def _from_repo_root(cls, p: Path) -> Path:
        # Relative paths in .env mean "from the repo root", whatever directory the app starts in.
        return p if p.is_absolute() else ROOT / p

    def secrets(self) -> list[str]:
        """Values the logger must never print."""
        return [
            s
            for s in (
                self.llm_api_key,
                self.embed_api_key,
                self.judge_api_key,
                self.qdrant_api_key,
                self.approver_token,
            )
            if len(s) > 3
        ]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Limits(Strict):
    max_steps: int = 8
    max_tool_calls: int = 12
    max_run_seconds: float = 120
    max_repeat_calls: int = 2
    max_repairs: int = 2
    max_calls_per_reply: int = 3
    max_incidents_per_run: int = 1


class LLM(Strict):
    temperature: float = 0
    timeout_s: float = 30
    max_attempts: int = 3


class Retry(Strict):
    base_delay_s: float = 0.2
    max_delay_s: float = 2.0


class ToolCfg(Strict):
    timeout_s: float = 5
    max_attempts: int = 3


class KB(Strict):
    collection: str = "ops_kb"
    top_k_dense: int = 10
    top_k_bm25: int = 10
    rrf_k: int = 60
    top_n: int = 3
    embed_timeout_s: float = 2.0  # query embedding; slower counts as failed


class BM25(Strict):
    k1: float = 1.2
    b: float = 0.75
    avg_doc_len: float = 120


class Approval(Strict):
    ttl_s: int = 900
    sweep_s: int = 30


class Output(Strict):
    max_tool_result_chars: int = 4000


class EvalThresholds(Strict):
    faithfulness: float = 0.7
    context_relevance: float = 0.5


class Eval(Strict):
    golden_set: str = "evals/kb_golden.jsonl"  # relative to the repo root
    online_default: bool = True
    relevancy_strictness: int = 1
    thresholds: EvalThresholds = EvalThresholds()


class Config(Strict):
    limits: Limits = Limits()
    llm: LLM = LLM()
    retry: Retry = Retry()
    tools: dict[str, ToolCfg] = {}
    kb: KB = KB()
    bm25: BM25 = BM25()
    approval: Approval = Approval()
    output: Output = Output()
    eval: Eval = Eval()

    def tool(self, name: str) -> ToolCfg:
        return self.tools.get(name, ToolCfg())


settings = Settings()
cfg = Config.model_validate(yaml.safe_load(settings.config_path.read_text()) or {})
