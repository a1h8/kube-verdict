"""
PR/MR-first remediation — phase 3: the invokable ``propose_patch`` use-case.

Ties phases 1 and 2 into one call that a surface (MCP tool, REST option, agent)
can invoke:

    verdict remediation + repo file
        → read current values.yaml         (GitProvider, phase 0 read side)
        → build declared patch + diff        (PatchBuilder, phase 1)
        → [open_pr] draft PR/MR              (ChangeProposer factory, phase 2)

Default is **dry-run**: with ``open_pr=False`` (or no token) it returns the diff
and never touches the remote — the reviewable patch without side effects. Opening
the draft PR/MR is opt-in and still never auto-merges. See ``docs/pr-mr-first.md``.
"""
from __future__ import annotations

from typing import Any

from ingestion.git_provider import make_provider
from remediation.change_proposer import make_change_proposer
from remediation.patch_builder import PatchBuilder, PatchProposal


def propose_patch(
    remediation: list[str],
    *,
    repo_url: str,
    file_path: str,
    base: str = "main",
    release: str = "",
    namespace: str = "",
    token: str | None = None,
    open_pr: bool = False,
    provider: str | None = None,
    values_yaml: str | None = None,
) -> dict[str, Any]:
    """Build a declared values.yaml patch from a verdict, optionally opening a PR/MR.

    Parameters
    ----------
    remediation:   the verdict's remediation commands (``helm upgrade --set`` lines
                   are turned into declarative values changes; others are ignored).
    repo_url:      GitOps repo holding the chart values.
    file_path:     path of the values file to patch within the repo.
    base:          base branch the change targets (default ``main``).
    token:         write token; required only when ``open_pr=True``.
    open_pr:       when True, open a **draft** PR/MR; otherwise return the patch only.
    values_yaml:   current file content; fetched from the repo when omitted.

    Returns a JSON-safe dict:
        {proposed, dry_run, reason, patch: {...}, change: {...} | None}
    ``proposed`` is True only when a draft PR/MR was actually opened.
    """
    if values_yaml is None:
        values_yaml = make_provider(repo_url, branch=base, token=token).get_file(file_path)
    if values_yaml is None:
        return {
            "proposed": False,
            "dry_run": not open_pr,
            "reason": f"could not read {file_path} from {repo_url}@{base}",
            "patch": None,
            "change": None,
        }

    patch: PatchProposal = PatchBuilder().build(
        remediation, values_yaml, file_path=file_path,
        release=release, namespace=namespace,
    )

    if patch.is_empty:
        return {
            "proposed": False,
            "dry_run": not open_pr,
            "reason": "no declarative values change (drift is live-only or no "
                      "`helm upgrade --set` in remediation)",
            "patch": patch.to_dict(),
            "change": None,
        }

    if not open_pr:
        return {
            "proposed": False,
            "dry_run": True,
            "reason": "dry-run — patch built, PR/MR not opened (open_pr=false)",
            "patch": patch.to_dict(),
            "change": None,
        }

    proposer = make_change_proposer(repo_url, base=base, token=token, provider=provider)
    change = proposer.propose(patch, draft=True)
    return {
        "proposed": True,
        "dry_run": False,
        "reason": f"draft {change.provider} change opened",
        "patch": patch.to_dict(),
        "change": change.to_dict(),
    }
