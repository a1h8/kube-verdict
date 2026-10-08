# Repository maintenance log

Operations on the repository itself (history rewrites, protection changes,
GitHub Support requests) that leave no trace in the code.

## 2026-09 → 2026-10 — personal email addresses removed from history

**Why.** Personal email addresses were exposed in `SECURITY.md`, in the Helm
chart maintainer fields (`helm/kube-verdict/Chart.yaml`,
`helm/kube-verdict/artifacthub-repo.yml`) and in commit author/committer
metadata.

**Round 1 (2026-09-21).** History rewritten to drop the address from
`SECURITY.md` (now `[redacted]`); all branches and tags force-pushed.
GitHub Support ticket **#4777914** opened to clear the stale `refs/pull/*`
refs and garbage-collect the old objects.

**Round 2 (2026-10-08).** A second personal address was still present
(metadata of 41 commits on GitHub, plus the Helm maintainer fields since
`b2e92d4`, 2026-06-18). Rewritten with `git filter-repo` (`--mailmap` +
`--replace-text`) to the public address `a1h8ah@gmail.com`, then:

- all branches and tags force-pushed;
- `main` protection temporarily relaxed (`allow_force_pushes` on,
  `enforce_admins` off) for the single push, then restored and diffed
  field-by-field against the saved settings;
- local clone realigned: unpushed commits rebased onto the new `main`,
  merged/local-only branches dropped, leftover `refs/original/*` removed,
  reflog expired and objects pruned.

**Still open.** `refs/pull/*` for PRs #6–#117 still point at pre-rewrite
commits; only GitHub Support can remove them. Requested option: delete the
internal references, keep the PRs and their comments (diffs become
unavailable).

**Rules going forward.**

- Local commits use the GitHub noreply address.
- Merges done on github.com use the account's primary email: keep
  "Keep my email addresses private" enabled in GitHub settings.
- Before pushing from any older clone, re-clone or realign it first —
  pushing old branches would reintroduce the removed history.
