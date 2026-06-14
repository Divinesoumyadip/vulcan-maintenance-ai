"""VULCAN configuration — paths, model selection, environment."""
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Load PROJECT_ROOT/.env if present (simple KEY=VALUE lines, no dependency).
# Shell-exported variables always win over the file.
_env_file = PROJECT_ROOT / ".env"
if _env_file.exists():
    for _line in _env_file.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if not _line or _line.startswith("#") or "=" not in _line:
            continue
        _k, _, _v = _line.partition("=")
        os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

DATA_DIR = PROJECT_ROOT / "data"
KNOWLEDGE_BASE_DIR = DATA_DIR / "knowledge_base"
SENSOR_DATA_DIR = DATA_DIR / "sensor_data"
SPARES_DB_PATH = DATA_DIR / "spares.json"
DELAY_LOG_PATH = DATA_DIR / "delay_log.csv"
FEEDBACK_DB_PATH = DATA_DIR / "feedback.sqlite3"
SYSTEM_PROMPT_PATH = PROJECT_ROOT / "vulcan" / "prompts" / "system_prompt.txt"

# LLM settings — configurable via environment.
# Check https://docs.claude.com/en/api/overview for current model names.
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
MAX_TOKENS = int(os.environ.get("VULCAN_MAX_TOKENS", "4096"))
MAX_TOOL_ROUNDS = int(os.environ.get("VULCAN_MAX_TOOL_ROUNDS", "8"))


# ───────────────────── v10 autonomy configuration ─────────────────────
# All read AT CALL TIME by their consumers (not frozen at import) so tests
# and operators can change behavior without restarting the process.

def _flag(name: str, default: str) -> bool:
    return os.environ.get(name, default).strip().lower() not in (
        "0", "false", "no", "off", "")


def daemon_autostart() -> bool:
    """v10: autonomy is the DEFAULT, not an opt-in. The daemon starts with
    the server unless explicitly disabled (VULCAN_DAEMON_AUTOSTART=0)."""
    return _flag("VULCAN_DAEMON_AUTOSTART", "1")


def daemon_interval() -> int:
    return max(5, int(os.environ.get("VULCAN_DAEMON_INTERVAL", "60")))


def rul_warn_hours() -> float:
    """Predictive-alert horizon: RUL below this → autonomous WARNING."""
    return float(os.environ.get("VULCAN_RUL_WARN_HOURS", "72"))


def rul_crit_hours() -> float:
    """Predictive-alert horizon: RUL below this → autonomous CRITICAL."""
    return float(os.environ.get("VULCAN_RUL_CRIT_HOURS", "24"))


def wo_sla_minutes() -> int:
    """A CRITICAL work order still OPEN after this many minutes triggers a
    one-time autonomous SLA-breach escalation notification."""
    return int(os.environ.get("VULCAN_WO_SLA_MIN", "60"))


def daemon_llm_enabled() -> bool:
    """If 1, the daemon attaches a full agent diagnostic to CRITICAL alerts
    (needs ANTHROPIC_API_KEY; budget-capped — see below). Default OFF so
    unattended monitoring never burns tokens without explicit consent."""
    return _flag("VULCAN_DAEMON_LLM", "0")


def daemon_llm_max_per_hour() -> int:
    return int(os.environ.get("VULCAN_DAEMON_LLM_MAX_PER_HOUR", "4"))


def webhook_url() -> str:
    """Optional external notification sink (Slack/Teams/SMS-gateway style
    webhook). Empty = JSONL ledger only."""
    return os.environ.get("VULCAN_WEBHOOK_URL", "").strip()


# ───────────────────── v11 production configuration ─────────────────────

def webhook_secret() -> str:
    """If set, webhook POSTs carry an HMAC-SHA256 X-Vulcan-Signature
    header so the receiver can verify authenticity."""
    return os.environ.get("VULCAN_WEBHOOK_SECRET", "")


def retention_days() -> int:
    """Alert files and notification rows older than this are pruned each
    cycle (0 disables pruning). Unbounded growth is an outage, slowly."""
    return int(os.environ.get("VULCAN_RETENTION_DAYS", "30"))


def max_alerts_per_cycle() -> int:
    """Alert-storm guard: above this many NEW alerts in ONE pass, the
    cycle emits a single roll-up report + one CRITICAL notification
    instead of flooding files/roles/work-orders. A storm means a systemic
    event (sensor flood, data fault, plant trip) — 200 individual tickets
    is noise, not action."""
    return int(os.environ.get("VULCAN_MAX_ALERTS_PER_CYCLE", "25"))


def health_port() -> int:
    """Port for the service-mode /healthz endpoint (0 disables)."""
    return int(os.environ.get("VULCAN_HEALTH_PORT", "8799"))


def log_level() -> str:
    return os.environ.get("VULCAN_LOG_LEVEL", "INFO").upper()


def validate_config() -> list[str]:
    """Fail-fast startup validation (v11). Returns human-readable errors;
    empty list = sane. A misconfigured autonomous system must refuse to
    start, not run wrong quietly."""
    errors: list[str] = []
    try:
        warn, crit = rul_warn_hours(), rul_crit_hours()
        if crit <= 0 or warn <= 0:
            errors.append("RUL horizons must be > 0 "
                          f"(warn={warn}, crit={crit})")
        elif crit >= warn:
            errors.append(f"VULCAN_RUL_CRIT_HOURS ({crit}) must be smaller "
                          f"than VULCAN_RUL_WARN_HOURS ({warn}) — CRITICAL "
                          "is the nearer horizon")
    except ValueError as exc:
        errors.append(f"RUL horizon env vars are not numbers: {exc}")
    try:
        raw_interval = int(os.environ.get("VULCAN_DAEMON_INTERVAL", "60"))
        if raw_interval < 5:
            errors.append("VULCAN_DAEMON_INTERVAL must be >= 5 seconds "
                          f"(got {raw_interval}; the runtime getter would "
                          "silently clamp it — fail loud instead)")
    except ValueError as exc:
        errors.append(f"VULCAN_DAEMON_INTERVAL invalid: {exc}")
    try:
        if wo_sla_minutes() <= 0:
            errors.append("VULCAN_WO_SLA_MIN must be > 0")
    except ValueError as exc:
        errors.append(f"VULCAN_WO_SLA_MIN invalid: {exc}")
    try:
        if max_alerts_per_cycle() < 1:
            errors.append("VULCAN_MAX_ALERTS_PER_CYCLE must be >= 1")
        if retention_days() < 0:
            errors.append("VULCAN_RETENTION_DAYS must be >= 0")
    except ValueError as exc:
        errors.append(f"storm/retention env vars invalid: {exc}")
    if webhook_url() and not webhook_url().lower().startswith(
            ("http://", "https://")):
        errors.append("VULCAN_WEBHOOK_URL must be an http(s) URL")
    prompt = PROJECT_ROOT / "vulcan" / "prompts" / "system_prompt.txt"
    if not prompt.exists():
        errors.append(f"system prompt missing: {prompt}")
    if not (SENSOR_DATA_DIR / "readings.csv").exists():
        errors.append(f"sensor readings missing: "
                      f"{SENSOR_DATA_DIR / 'readings.csv'}")
    return errors


def load_system_prompt() -> str:
    return SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
