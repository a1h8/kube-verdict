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

``--set`` is read the way Helm reads it (docs/fix-plan-001.md, H69): unescaped
commas separate pairs, ``a.b[0].c`` indexes lists, only integers, booleans and
``null`` are converted (``1.10`` stays the string Helm keeps), ``--set-string``
converts nothing, and one patch covers one release. A scalar replaced by a
mapping is applied, as Helm does, and reported in ``warnings``.

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
_SET_RE = re.compile(r"--set(?P<string>-string)?(?:\s+|=)(?P<kv>[^\s]+)")
_KEY_PART_RE = re.compile(r"^(?P<name>[^\[\]]+)(?P<idx>(?:\[\d+\])*)$")
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
    warnings: list[str] = field(default_factory=list)

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
            "warnings": list(self.warnings),
        }


def _coerce(value: str) -> Any:
    """A --set value as Helm types it: integers, booleans and null; everything else a string.

    Helm keeps ``1.10`` a string (checked with ``helm template``): converting it to the float
    1.1 changed an image tag's version.
    """
    low = value.lower()
    if low in ("true", "false"):
        return low == "true"
    if low == "null":
        return None
    if re.fullmatch(r"-?(?:0|[1-9]\d*)", value):
        return int(value)
    return value


def _split_unescaped(text: str, sep: str) -> list[str]:
    """Split on ``sep`` unless backslash-escaped; unescape it in the parts."""
    parts, buf, i = [], "", 0
    while i < len(text):
        if text[i] == "\\" and i + 1 < len(text) and text[i + 1] == sep:
            buf += sep
            i += 2
            continue
        if text[i] == sep:
            parts.append(buf)
            buf = ""
        else:
            buf += text[i]
        i += 1
    parts.append(buf)
    return parts


def _key_path(dotted_key: str) -> list[str | int]:
    """``a.b[0].c`` → ``["a", "b", 0, "c"]``; an escaped dot stays in the name."""
    path: list[str | int] = []
    for part in _split_unescaped(dotted_key, "."):
        m = _KEY_PART_RE.match(part)
        if not m:
            raise ValueError(f"cannot read --set key {dotted_key!r}")
        path.append(m.group("name"))
        path.extend(int(n) for n in re.findall(r"\[(\d+)\]", m.group("idx")))
    return path


def _set_path(tree: dict, path: list[str | int], value: Any, warnings: list[str]) -> None:
    """Set ``value`` at ``path``, creating mappings and lists (padded with null) like Helm."""
    node: Any = tree
    for here, nxt in zip(path, path[1:]):
        empty: Any = [] if isinstance(nxt, int) else {}
        if isinstance(here, int):
            while len(node) <= here:
                node.append(None)
            child = node[here]
        else:
            child = node.get(here)
        if not isinstance(child, type(empty)):
            if child is not None:
                warnings.append(f"{here}: {child!r} replaced by a "
                                f"{'list' if isinstance(empty, list) else 'mapping'}")
            child = empty
            node[here] = child
        node = child
    last = path[-1]
    if isinstance(last, int):
        while len(node) <= last:
            node.append(None)
    node[last] = value


def parse_set_overrides(
    remediation: list[str], release: str = "",
) -> tuple[dict[str, Any], str, str]:
    """Extract ``{helm_key: value}`` plus release/namespace from ``helm upgrade`` lines.

    Only ``helm upgrade`` commands contribute (kubectl fixes are imperative and have no
    values.yaml equivalent), and only those of one release: ``release``, or the first one met;
    another release's overrides used to be merged into the same values file. Later ``--set`` of
    the same key wins.
    """
    changes: dict[str, Any] = {}
    namespace = ""
    for cmd in remediation or []:
        m = _HELM_UPGRADE_RE.search(cmd)
        if not m:
            continue
        if not release:
            release = m.group("release")
        elif m.group("release") != release:
            continue
        ns = _NS_RE.search(cmd)
        if ns and not namespace:
            namespace = ns.group("ns")
        for sm in _SET_RE.finditer(cmd):
            for pair in _split_unescaped(sm.group("kv"), ","):
                if "=" not in pair:
                    continue
                key, _, raw = pair.partition("=")
                changes[key] = raw if sm.group("string") else _coerce(raw)
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
        changes, parsed_release, parsed_ns = parse_set_overrides(remediation, release)
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
        warnings: list[str] = []
        for key, value in changes.items():
            _set_path(tree, _key_path(key), value, warnings)

        # Gate on a *semantic* change: if the overrides already match the declared
        # state (e.g. h012, where the drift is live-only), don't propose a patch
        # that would only re-serialize the file. Comparing parsed trees also keeps
        # us from emitting spurious formatting churn.
        if tree == base:
            return PatchProposal(
                release=release, namespace=namespace, file_path=file_path,
                changes=changes, diff="", new_content=original,
                source_remediation=list(remediation or []), warnings=warnings,
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
            source_remediation=list(remediation or []), warnings=warnings,
        )
