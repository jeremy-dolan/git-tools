# Tests

Run the full suite from the project root:

```sh
python3 -m unittest discover -s tests -v
```

Run only the git-overview contract tests:

```sh
python3 -m unittest discover -s tests -p 'test_git_overview.py' -v
```

The tests use Python's standard library and isolated temporary Git repositories.
The full suite requires Python 3.8+ and Git 2.46+. The git-reword SSH signing
tests skip when `ssh-keygen` is unavailable. git-overview itself uses only Python,
Git, and optional `gh`; it needs neither Bash nor external timeout or terminal
utilities. Some test fixtures use `/bin/sh` for hooks.

The git-overview tests disable user/system Git configuration and provide a
controlled command search path. Real Git handles repository operations; local
shims supply remote advertisements and GitHub PR JSON responses and inject query
errors. `COLUMNS` controls terminal width. They make no network calls and do not
require `gh`. Timeout cases run sleeping query processes, measure the native
5-second Git and 6-second gh deadlines (with scheduling tolerance), and verify
that those processes have been killed and reaped. These cases add about eleven
seconds to the suite; they do not rely on an external timeout utility.

Coverage includes repository preservation (file bytes, modes, and mtimes),
offline behavior, upstream freshness and divergence, branch limits, worktree
status, unusual names, operation indicators, stashes, tags, reflog, PR states,
query failures, colors, and narrow output. Assertions target observable behavior
rather than a complete output snapshot, allowing implementation changes.

The suite has 102 tests, including 38 git-overview integration tests. The two
former expected failures now pass: pipe characters preserve branch fields, and
newlines in worktree paths no longer make existing checkouts appear missing.
Additional rewrite regressions cover NUL-delimited rename records, trailing
newlines and non-UTF-8 path bytes, multiline worktree lock reasons, malformed
PR JSON and null review decisions, tags without dates, noninteractive
authentication, failed worktree queries, unavailable ahead/behind counts, and
terminal-width limits without Unix utilities.

The display still measures characters rather than terminal cell widths, so wide
or combining Unicode glyphs can affect alignment. Local status scans run once
per displayed worktree and have no timeout; the deadlines apply to remote and
PR queries. Tests exercise local shims, not live GitHub or credential services.
