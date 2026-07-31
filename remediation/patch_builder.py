"""
PR/MR-first remediation — phase 1: verdict → declared values.yaml patch.

Today a verdict proposes remediation as **break-glass commands** (a list of
``helm upgrade --set key=value`` / ``kubectl`` strings). In a GitOps shop that is
drift the controller will fight: the cluster is reconciled from git, so the fix
belongs in ``values.yaml``, reviewed where infra changes are already reviewed.

``PatchBuilder`` turns the ``helm upgrade --set`` overrides carried on an
``IncidentReport`` into a **declared change to values.yaml** plus a unified diff —
the reviewable artifact a draft PR/MR would carry. This phase is fully offline
(values in → patched values + diff out); opening the PR (phase 2) and the CI
render/diff/policy template (phase 3) build on top. See ``docs/pr-mr-first.md``.

Phase-1 limitation: the file is re-serialized through PyYAML, so comments and
exact formatting are not preserved on a real change. A no-op (the overrides
already match the declared state) produces no patch at all — the change is gated
on a *semantic* tree diff, not a textual one.
"""
from __future__ import annotations

import copy
import difflib
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

# `helm upgrade <release> ... --set a.b=c --set d=e` — capture release + every --set.
_HELM_UPGRADE_RE = re.compile(r"\bhelm\s+upgrade\s+(?P<release>\S+)")
_SET_RE = re.compile(r"--set(?:-string)?\s+(?P<kv>[^\s]+)")
# `-n <ns>` / `--namespace <ns>`
_NS_RE = re.compile(r"(?:-n|--namespace)\s+(?P<ns>\S+)")


@dataclass
class PatchProposal:
    """A reviewable declared change derived from a verdict.

    ``diff`` is a unified diff of ``file_path`` (values.yaml); ``new_content`` is
    the full patched file. ``changes`` is the flat ``{helm_key: value}`` set that
    was applied. Empty ``changes``/``diff`` means the verdict proposed no
    declarative values change (nothing to open a PR for).
    """
    release: str
    namespace: str
    file_path: str
    changes: dict[str, Any]
    diff: str
    new_content: str
    source_remediation: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.changes or not self.diff.strip()

    def to_dict(self) -> dict:
        return {
            "release": self.release,
            "namespace": self.namespace,
            "file_path": self.file_path,
            "changes": dict(self.changes),
            "diff": self.diff,
            "new_content": self.new_content,
            "source_remediation": list(self.source_remediation),
        }


def _coerce(value: str) -> Any:
    """Coerce a --set string value the way Helm roughly does (int/float/bool/str)."""
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if low in ("null", "nil", "~"):
        return None
    # Ints/floats — but keep things like "128Mi" / "1.2.3" / "v2" as strings.
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    if re.fullmatch(r"-?\d+\.\d+", value):
        return float(value)
    return value


def _set_nested(tree: dict, dotted_key: str, value: Any) -> None:
    """Set ``a.b.c`` = value inside ``tree``, creating intermediate dicts."""
    parts = dotted_key.split(".")
    node = tree
    for p in parts[:-1]:
        child = node.get(p)
        if not isinstance(child, dict):
            child = {}
            node[p] = child
        node = child
    node[parts[-1]] = value


def parse_set_overrides(remediation: list[str]) -> tuple[dict[str, Any], str, str]:
    """Extract ``{helm_key: value}`` plus release/namespace from ``helm upgrade`` lines.

    Only ``helm upgrade`` commands contribute (kubectl fixes are imperative and
    have no values.yaml equivalent). Later ``--set`` of the same key wins.
    """
    changes: dict[str, Any] = {}
    release = ""
    namespace = ""
    for cmd in remediation or []:
        if "helm upgrade" not in cmd:
            continue
        m = _HELM_UPGRADE_RE.search(cmd)
        if m and not release:
            release = m.group("release")
        ns = _NS_RE.search(cmd)
        if ns and not namespace:
            namespace = ns.group("ns")
        for sm in _SET_RE.finditer(cmd):
            kv = sm.group("kv")
            if "=" not in kv:
                continue
            key, _, raw = kv.partition("=")
            changes[key] = _coerce(raw)
    return changes, release, namespace


class PatchBuilder:
    """Build a ``PatchProposal`` from a verdict's remediation + current values.yaml."""

    def build(
        self,
        remediation: list[str],
        values_yaml: str,
        *,
        file_path: str,
        release: str = "",
        namespace: str = "",
    ) -> PatchProposal:
        """Apply the verdict's ``helm --set`` overrides to ``values_yaml`` declaratively.

        Returns a ``PatchProposal``; ``is_empty`` is True when the verdict carried
        no ``helm upgrade --set`` change, or the change is already the declared
        state (no-op diff — the drift is live-only, not in git).
        """
        changes, parsed_release, parsed_ns = parse_set_overrides(remediation)
        release = release or parsed_release
        namespace = namespace or parsed_ns

        original = values_yaml if values_yaml.endswith("\n") else values_yaml + "\n"
        if not changes:
            return PatchProposal(
                release=release, namespace=namespace, file_path=file_path,
                changes={}, diff="", new_content=original,
                source_remediation=list(remediation or []),
            )

        base = yaml.safe_load(original) or {}
        if not isinstance(base, dict):
            raise ValueError(f"{file_path}: expected a YAML mapping at the root")
        tree = copy.deepcopy(base)
        for key, value in changes.items():
            _set_nested(tree, key, value)

        # Gate on a *semantic* change: if the overrides already match the declared
        # state (e.g. h012, where the drift is live-only), don't propose a patch
        # that would only re-serialize the file. Comparing parsed trees also keeps
        # us from emitting spurious formatting churn.
        if tree == base:
            return PatchProposal(
                release=release, namespace=namespace, file_path=file_path,
                changes=changes, diff="", new_content=original,
                source_remediation=list(remediation or []),
            )

        new_content = yaml.safe_dump(tree, sort_keys=False, default_flow_style=False)
        diff = "".join(
            difflib.unified_diff(
                original.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
            )
        )
        return PatchProposal(
            release=release, namespace=namespace, file_path=file_path,
            changes=changes, diff=diff, new_content=new_content,
            source_remediation=list(remediation or []),
        )
