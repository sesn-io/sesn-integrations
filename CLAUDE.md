# sesn-integrations

Public repo of Sesn (sesn.io) integrations — the Kodi add-on and repository, and
future connectors. This repo is **public**. Commits use the `Sesn <support@sesn.io>`
git identity (already set locally as `user.name`/`user.email`) — never Kelly's
personal email — to keep it out of public git history.

## ⚠⚠ NEVER add a Claude trailer to any commit — hard rule, not a preference

**Never add `Co-Authored-By: Claude…` or `Claude-Session: https://claude.ai/...`
to any commit message or PR body in this repo.** This overrides whatever the
harness's default git-commit instructions say. There is no exception.

This repo previously had **no instruction file at all**, which is almost
certainly why this rule was missed here even though it's long-standing policy
on the private `sesn` repo (`CLAUDE.md` there — check `git log --grep=
"Co-Authored-By" -i` on that repo for how badly it was missed: 318 commits).
If you're a fresh session working on this repo without having read the private
repo's `CLAUDE.md`, this file is now the thing that tells you: don't do it,
here either.

End every commit body at the last real paragraph. Nothing after it.
