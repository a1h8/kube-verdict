from __future__ import annotations
import logging
import re
from dataclasses import dataclass

from kubernetes import client as k8s_client
from kubernetes.client import ApiException

log = logging.getLogger(__name__)

# Known API deprecation / promotion thresholds
# (major, minor) → what changed
_CHANGELOG = {
    (1, 16): "apps/v1 becomes the only supported version for Deployments/StatefulSets/DaemonSets",
    (1, 19): "networking.k8s.io/v1 introduced for Ingress",
    (1, 21): "batch/v1 CronJob promoted; batch/v1beta1 deprecated",
    (1, 22): "networking.k8s.io/v1beta1 Ingress removed; several beta APIs removed",
    (1, 25): "PodSecurityPolicy removed",
    (1, 26): "autoscaling/v2 HPA stable; autoscaling/v2beta2 removed",
    (1, 29): "flowcontrol.apiserver.k8s.io/v1 stable",
}


@dataclass(frozen=True)
class KubeVersion:
    major: int
    minor: int
    git_version: str  # raw string, e.g. "v1.28.3+k3s1"
    platform: str = ""

    # ------------------------------------------------------------------
    # Comparison
    # ------------------------------------------------------------------

    def gte(self, major: int, minor: int) -> bool:
        return (self.major, self.minor) >= (major, minor)

    def lt(self, major: int, minor: int) -> bool:
        return (self.major, self.minor) < (major, minor)

    # ------------------------------------------------------------------
    # Feature flags derived from version
    # ------------------------------------------------------------------

    @property
    def ingress_api_version(self) -> str:
        """networking.k8s.io/v1 from 1.19+; v1beta1 before."""
        return "networking.k8s.io/v1" if self.gte(1, 19) else "networking.k8s.io/v1beta1"

    @property
    def cronjob_api_version(self) -> str:
        """batch/v1 from 1.21+; batch/v1beta1 before."""
        return "batch/v1" if self.gte(1, 21) else "batch/v1beta1"

    @property
    def hpa_api_version(self) -> str:
        """autoscaling/v2 from 1.26+."""
        return "autoscaling/v2" if self.gte(1, 26) else "autoscaling/v2beta2"

    @property
    def has_pod_security_policy(self) -> bool:
        return self.lt(1, 25)

    @property
    def supports_networking_v1_ingress(self) -> bool:
        return self.gte(1, 19)

    # ------------------------------------------------------------------
    # Human-readable
    # ------------------------------------------------------------------

    def __str__(self) -> str:
        return self.git_version

    def changelog_notes(self) -> list[str]:
        notes = []
        for (maj, min_), note in sorted(_CHANGELOG.items()):
            if self.gte(maj, min_):
                notes.append(f"  >= {maj}.{min_}: {note}")
        return notes


def detect_version(api_client: k8s_client.ApiClient) -> KubeVersion:
    """
    Fetches /version from the API server and returns a KubeVersion.
    Falls back to (1, 0) if the endpoint is unreachable (air-gap, permissions).
    """
    version_api = k8s_client.VersionApi(api_client)
    try:
        info = version_api.get_code()
        major = _parse_int(info.major)
        minor = _parse_int(info.minor)
        return KubeVersion(
            major=major,
            minor=minor,
            git_version=info.git_version or f"v{major}.{minor}",
            platform=info.platform or "",
        )
    except ApiException as exc:
        log.warning("Could not detect server version (/version returned %s), defaulting to 1.19", exc.status)
        return KubeVersion(major=1, minor=19, git_version="v1.19.0-unknown")
    except Exception as exc:
        log.warning("Version detection failed: %s — defaulting to 1.19", exc)
        return KubeVersion(major=1, minor=19, git_version="v1.19.0-unknown")


def _parse_int(value: str | None) -> int:
    if not value:
        return 0
    # Strip non-numeric suffixes like "+" in "1.28+"
    match = re.match(r"(\d+)", str(value))
    return int(match.group(1)) if match else 0
