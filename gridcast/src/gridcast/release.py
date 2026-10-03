"""Release manifests baked into each container image.

Every image carries `/app/release.json` describing the service, its version, the git revision
it was built from, its changelog and the release's behaviour flags. The flags are how a
release changes behaviour (for example the feature-service 1.7.0 aggregation change), so the
difference between two versions is visible in the image, the deployment history and the
service's `/release` endpoint, exactly as it would be for a real code change.
"""

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class Release(BaseModel):
    service: str = "gridcast-dev"
    version: str = "0.0.0-dev"
    revision: str = "unknown"
    built_at: str | None = None
    changelog: list[str] = Field(default_factory=list)
    flags: dict[str, Any] = Field(default_factory=dict)

    def flag(self, name: str, default: Any = None) -> Any:
        return self.flags.get(name, default)


@lru_cache
def load_release(path: str = "/app/release.json", service: str | None = None) -> Release:
    """Read the baked manifest; fall back to a dev release when running from source."""
    file = Path(path)
    if file.is_file():
        return Release.model_validate(json.loads(file.read_text()))
    return Release(service=service or "gridcast-dev")
