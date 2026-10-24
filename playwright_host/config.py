from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os


DEFAULT_PROJECT_ID = "git-agent-harness"
DEFAULT_BRIDGE_URL = "http://127.0.0.1:8765/api"
DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_RUNTIME_ROOT = Path(os.environ.get("CAH_PLAYWRIGHT_ROOT", r"__CAH_BROWSER_ROOT__"))

TASK_CELL = {
    "display_name": "CAH Task Cell",
    "project_key": "g-p-CAHTASKCELL",
    "project_root_url": "https://chatgpt.com/g/g-p-CAHTASKCELL-cah-task-cell/project",
}
BOOTSTRAP_LANES = [
    {
        "lane_id": "lane-00",
        "display_name": "CAH Sandbox0",
        "project_key": "g-p-CAHLANE00",
        "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE00-cah-sandbox0/project",
        "enabled": True,
    },
    {
        "lane_id": "lane-01",
        "display_name": "CAH Sandbox1",
        "project_key": "g-p-CAHLANE01",
        "project_root_url": "https://chatgpt.com/g/g-p-CAHLANE01-cah-sandbox1/project",
        "enabled": True,
    },
]


@dataclass(frozen=True)
class HostConfig:
    bridge_url: str = DEFAULT_BRIDGE_URL
    project_id: str = DEFAULT_PROJECT_ID
    cdp_url: str = DEFAULT_CDP_URL
    runtime_root: Path = field(default_factory=lambda: DEFAULT_RUNTIME_ROOT)
    wake_poll_seconds: float = 1.0
    maintenance_seconds: float = 60.0

    @property
    def state_path(self) -> Path:
        return self.runtime_root / "state.json"

    @property
    def browser_endpoint_path(self) -> Path:
        return self.runtime_root / "browser-endpoint.json"
