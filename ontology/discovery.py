from __future__ import annotations
import logging
from typing import Iterator

from kubernetes import client as k8s_client
from kubernetes.client import ApiException

from .dynamic_entity import APIResourceInfo, GenericEntity

log = logging.getLogger(__name__)

# Core resource kinds that have typed entities in entities.py
# The collector still handles these via CoreV1Api / AppsV1Api for
# richer field extraction; discovery skips them to avoid duplicates.
_TYPED_KINDS = {
    "Namespace", "Node", "Pod", "Deployment", "StatefulSet", "DaemonSet",
    "ReplicaSet", "Service", "Ingress", "ConfigMap", "Secret",
    "PersistentVolume", "PersistentVolumeClaim", "ServiceAccount",
    "Event",
}


class APIServerDiscovery:
    """
    Queries the Kubernetes API server discovery endpoints (/api and /apis)
    to enumerate every resource kind the server knows about — including CRDs.

    This is the source of truth for the ontology vocabulary: we never
    hardcode resource kinds beyond the typed core set.
    """

    def __init__(self, api_client: k8s_client.ApiClient) -> None:
        self._client = api_client
        self._core_api = k8s_client.CoreV1Api(api_client)
        self._apis_api = k8s_client.ApisApi(api_client)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def list_all_resources(self, skip_kinds: set[str] | None = None) -> list[APIResourceInfo]:
        """
        Returns the full list of resource kinds registered on the server.
        Skips sub-resources (e.g. pods/log, pods/exec) and non-listable kinds.
        """
        skip = skip_kinds or set()
        resources: list[APIResourceInfo] = []

        resources.extend(self._discover_core(skip))
        resources.extend(self._discover_api_groups(skip))

        log.info("API server exposes %d listable resource kind(s)", len(resources))
        return resources

    def _discover_core(self, skip: set[str]) -> list[APIResourceInfo]:
        """Discovers resources under /api/v1 (core group)."""
        result = []
        try:
            resource_list = self._core_api.get_api_resources()
            for r in resource_list.resources:
                if "/" in r.name:  # sub-resource
                    continue
                if r.kind in skip:
                    continue
                if "list" not in (r.verbs or []):
                    continue
                result.append(APIResourceInfo(
                    group="",
                    version="v1",
                    kind=r.kind,
                    plural=r.name,
                    namespaced=r.namespaced,
                    verbs=list(r.verbs or []),
                    short_names=list(r.short_names or []),
                    categories=list(r.categories or []),
                ))
        except ApiException as exc:
            log.warning("Failed to discover core API resources: %s", exc)
        return result

    def _discover_api_groups(self, skip: set[str]) -> list[APIResourceInfo]:
        """Discovers resources under /apis (all non-core API groups)."""
        result = []
        try:
            groups = self._apis_api.get_api_versions()
        except ApiException as exc:
            log.warning("Failed to list API groups: %s", exc)
            return result

        for group in groups.groups:
            preferred = group.preferred_version
            group_name = group.name
            version = preferred.version

            try:
                k8s_client.CustomObjectsApi(
                    self._client
                ).get_api_resources.__func__  # not available this way
            except Exception:
                pass

            # Use the raw API to call /{group}/{version}
            try:
                path = f"/apis/{group_name}/{version}"
                resp = self._client.call_api(
                    path, "GET",
                    response_types_map={200: "object"},
                    auth_settings=["BearerToken"],
                    _return_http_data_only=True,
                )
                for r in resp.get("resources", []):
                    if "/" in r.get("name", ""):  # sub-resource
                        continue
                    kind = r.get("kind", "")
                    if kind in skip:
                        continue
                    verbs = r.get("verbs") or []
                    if "list" not in verbs:
                        continue
                    result.append(APIResourceInfo(
                        group=group_name,
                        version=version,
                        kind=kind,
                        plural=r.get("name", ""),
                        namespaced=r.get("namespaced", False),
                        verbs=list(verbs),
                        short_names=list(r.get("shortNames") or []),
                        categories=list(r.get("categories") or []),
                    ))
            except ApiException as exc:
                log.debug("Skipping group %s/%s: %s", group_name, version, exc.status)

        return result

    # ------------------------------------------------------------------
    # Fetching instances of any resource kind
    # ------------------------------------------------------------------

    def fetch_resources(
        self,
        resource: APIResourceInfo,
        namespaces: list[str] | None = None,
    ) -> Iterator[GenericEntity]:
        """
        Fetches all instances of a given resource kind via the raw REST API.
        Yields GenericEntity objects built directly from the API response.
        """
        if resource.namespaced:
            target_ns = namespaces or [None]
            for ns in target_ns:
                yield from self._list_namespaced(resource, ns)
        elif not namespaces:
            # Only fetch cluster-scoped resources when no namespace filter is set.
            # When a namespace filter is active, cluster-scoped resources (ClusterRole,
            # APIService, FlowSchema, etc.) are irrelevant and just add noise.
            yield from self._list_cluster_scoped(resource)

    def _list_namespaced(
        self, resource: APIResourceInfo, namespace: str | None
    ) -> Iterator[GenericEntity]:
        if resource.group:
            path = (
                f"/apis/{resource.group}/{resource.version}"
                f"/namespaces/{namespace}/{resource.plural}"
                if namespace
                else f"/apis/{resource.group}/{resource.version}/{resource.plural}"
            )
        else:
            path = (
                f"/api/{resource.version}/namespaces/{namespace}/{resource.plural}"
                if namespace
                else f"/api/{resource.version}/{resource.plural}"
            )
        yield from self._call_list(path, resource)

    def _list_cluster_scoped(self, resource: APIResourceInfo) -> Iterator[GenericEntity]:
        if resource.group:
            path = f"/apis/{resource.group}/{resource.version}/{resource.plural}"
        else:
            path = f"/api/{resource.version}/{resource.plural}"
        yield from self._call_list(path, resource)

    def _call_list(self, path: str, resource: APIResourceInfo) -> Iterator[GenericEntity]:
        try:
            resp = self._client.call_api(
                path, "GET",
                response_types_map={200: "object"},
                auth_settings=["BearerToken"],
                _return_http_data_only=True,
            )
            for item in resp.get("items") or []:
                try:
                    yield GenericEntity.from_api_object(item, resource)
                except Exception as exc:
                    log.debug("Could not parse %s object: %s", resource.kind, exc)
        except ApiException as exc:
            log.debug("Cannot list %s at %s: %s", resource.kind, path, exc.status)

    # ------------------------------------------------------------------
    # Vocabulary summary
    # ------------------------------------------------------------------

    def vocabulary_summary(self, resources: list[APIResourceInfo]) -> str:
        lines = [f"API server vocabulary: {len(resources)} resource kind(s)\n"]
        groups: dict[str, list[str]] = {}
        for r in resources:
            groups.setdefault(r.group or "core", []).append(r.kind)
        for group, kinds in sorted(groups.items()):
            lines.append(f"  [{group}]  {', '.join(sorted(kinds))}")
        return "\n".join(lines)
