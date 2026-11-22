"""Settings from environment variables, with optional .env file support."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")

WATSONX_APIKEY = os.environ.get("WATSONX_APIKEY", "")
WATSONX_PROJECT_ID = os.environ.get("WATSONX_PROJECT_ID", "")
WATSONX_URL = os.environ.get("WATSONX_URL", "https://us-south.ml.cloud.ibm.com").rstrip("/")
WATSONX_MODEL_ID = os.environ.get("WATSONX_MODEL_ID", "ibm/granite-4-h-small")
WATSONX_API_VERSION = os.environ.get("WATSONX_API_VERSION", "2024-05-31")
IAM_URL = os.environ.get("IBM_IAM_URL", "https://iam.cloud.ibm.com/identity/token")

# auto: use watsonx when credentials are present, otherwise the offline mock agent.
_mode = os.environ.get("AGENT_MODE", "auto").lower()
if _mode == "auto":
    _mode = "watsonx" if (WATSONX_APIKEY and WATSONX_PROJECT_ID) else "mock"
AGENT_MODE = _mode

MOCK_STEP_DELAY = float(os.environ.get("MOCK_STEP_DELAY", "0.7"))
MAX_STEPS_PER_RUN =int(os.environ.get("MAX_STEPS_PER_RUN", "25"))
PORT = int(os.environ.get("PORT", "8000"))
CASES_DIR = ROOT / "cases"
