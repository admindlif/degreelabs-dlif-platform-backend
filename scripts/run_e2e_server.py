"""Start FastAPI against the strictly isolated Playwright database."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from e2e_database import configure_e2e_environment


configure_e2e_environment()

from e2e_auth_fixture import seed  # noqa: E402


seed()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8001, log_level="warning")