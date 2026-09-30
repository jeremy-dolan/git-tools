# Tests

Run the full suite from the project root:

```sh
python3 -m unittest discover -s tests -v
```

Run only the git-overview contract tests:

```sh
python3 -m unittest discover -s tests -p 'test_git_overview.py' -v
```

The tests cover git-reword and git-overview using Python's standard library and
isolated temporary Git repositories. The full suite requires Python 3.8+ and
Git 2.46+; some fixtures use `/bin/sh` for hooks. SSH signing tests skip when
`ssh-keygen` is unavailable. The git-overview tests use local shims for remote
and GitHub responses, make no network calls, and do not require `gh`.
