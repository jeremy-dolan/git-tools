"""Behavioral contract for git-overview, independent of its implementation language.

Run: python3 -m unittest discover -s tests -p 'test_git_overview.py' -v
Uses real Git in temporary repositories, no network or user Git configuration.
Command shims control remote answers and query failures; COLUMNS controls width.
"""

import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "bin" / "git-overview"
REAL_GIT = shutil.which("git")
# Relative ages avoid depending on whether the implementation uses date(1) or
# an in-process clock. Fixtures stay well away from the asserted age boundaries.
NOW = int(time.time())
SECTIONS = ("HEAD", "UNCOMMITTED", "BRANCHES", "WORKTREES", "STASHES",
            "TAGS", "OPEN PRS", "RECENT (reflog)")
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@unittest.skipUnless(REAL_GIT, "requires Git")
class OverviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-git-overview-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.calls = self.base / "calls.jsonl"
        # An allowlisted PATH also makes the missing-gh case independent of
        # whether the developer happens to have GitHub CLI installed.
        for name in ("sh",):
            executable = shutil.which(name)
            if not executable:
                self.skipTest(f"requires {name}")
            (self.bin / name).symlink_to(executable)
        (self.bin / "python3").symlink_to(sys.executable)
        self.env = {k: v for k, v in os.environ.items()
                    if not k.startswith(("GIT_", "GH_", "OVERVIEW_TEST_", "BASH_FUNC_"))
                    and k not in ("BASH_ENV", "ENV", "NO_COLOR", "CDPATH")}
        self.env.update(PATH=str(self.bin), HOME=str(self.base), LC_ALL="C",
                        TZ="UTC", TERM="xterm", COLUMNS="100", GIT_CONFIG_NOSYSTEM="1",
                        GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0",
                        GIT_AUTHOR_DATE=f"{NOW - 3660} +0000",
                        GIT_COMMITTER_DATE=f"{NOW - 3660} +0000",
                        OVERVIEW_TEST_CALLS=str(self.calls))
        self.shim("git", f"""
args = sys.argv[1:]
record('git', args)
if 'ls-remote' in args:
    record('remote-env', [os.environ.get(k) for k in
           ('GIT_TERMINAL_PROMPT', 'GIT_SSH_COMMAND', 'GCM_INTERACTIVE')])
    if os.environ.get('OVERVIEW_TEST_TIMEOUT') == 'git':
        import time
        record('timeout-pid', [os.getpid()])
        time.sleep(30)
    print(os.environ.get('OVERVIEW_TEST_REMOTE', ''), end='')
    sys.exit(int(os.environ.get('OVERVIEW_TEST_REMOTE_RC', '99')))
if any(cmd in args for cmd in ('fetch', 'push', 'clone')):
    sys.exit('unexpected network-capable Git command')
fail = json.loads(os.environ.get('OVERVIEW_TEST_FAIL', '[]'))
if fail and all(arg in args for arg in fail):
    sys.exit(71)
os.execv({REAL_GIT!r}, [{REAL_GIT!r}, *args])
""")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Overview Tester")
        self.git("config", "user.email", "overview@example.com")
        self.commit("initial subject", "initial\n")
        self.root = self.oid()

    def shim(self, name, body):
        path = self.bin / name
        path.write_text(f"#!{sys.executable}\nimport json, os, sys\n"
                        "def record(tool, args):\n"
                        "    with open(os.environ['OVERVIEW_TEST_CALLS'], 'a') as f:\n"
                        "        f.write(json.dumps([tool, args]) + '\\n')\n" + body)
        path.chmod(0o755)

    def git(self, *args, cwd=None, check=True, env=None):
        result = subprocess.run([REAL_GIT, *args], cwd=cwd or self.repo,
                                env=dict(self.env, **(env or {})),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=15)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout.decode() if check else result

    def commit(self, message, contents=None, age=3660, cwd=None):
        if contents is not None:
            (Path(cwd or self.repo) / "file").write_text(contents)
            self.git("add", "file", cwd=cwd)
        self.git("commit", "--allow-empty", "-qm", message, cwd=cwd,
                 env={"GIT_AUTHOR_DATE": f"{NOW - age} +0000",
                      "GIT_COMMITTER_DATE": f"{NOW - age} +0000"})

    def oid(self, ref="HEAD"):
        return self.git("rev-parse", ref).strip()

    def run_overview(self, *args, env=None, cwd=None, ok=True):
        result = subprocess.run([str(SCRIPT), *args], cwd=cwd or self.repo,
                                env=dict(self.env, **(env or {})), input=b"",
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=20)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
            self.assertEqual(result.stderr, b"", result.stderr.decode(errors="replace"))
        return result

    def output(self, *args, **kwargs):
        return self.run_overview(*args, **kwargs).stdout.decode()

    def section(self, output, name):
        lines = ANSI.sub("", output).splitlines()
        result = []
        active = False
        for line in lines:
            header = next((s for s in SECTIONS if line.lstrip().startswith(s + " ─")), None)
            if header:
                if active:
                    break
                active = header == name
            elif active:
                result.append(line)
        self.assertTrue(active, f"missing section {name}:\n{output}")
        return "\n".join(result).strip("\n")

    def recorded(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def assert_timeout(self, start, seconds):
        elapsed = time.monotonic() - start
        self.assertGreaterEqual(elapsed, seconds - 0.2)
        self.assertLess(elapsed, seconds + 8)
        pids = [args[0] for tool, args in self.recorded() if tool == "timeout-pid"]
        self.assertTrue(pids, "the slow query must actually start")
        for pid in pids:
            with self.assertRaises(ProcessLookupError):
                os.kill(pid, 0)

    def remote(self, tracking=True):
        # Even an accidental bypass cannot reach a real network endpoint.
        self.git("remote", "add", "origin", str(self.base / "absent-remote"))
        self.git("config", "branch.main.remote", "origin")
        self.git("config", "branch.main.merge", "refs/heads/main")
        if tracking:
            self.git("update-ref", "refs/remotes/origin/main", self.root)
        fetch_head = self.repo / ".git" / "FETCH_HEAD"
        fetch_head.write_text(f"{self.root}\t\tbranch 'main' of origin\n")
        os.utime(fetch_head, (NOW - 172860, NOW - 172860))

    def gh(self, prs=(), rc=0):
        self.env["OVERVIEW_TEST_PRS"] = json.dumps(prs)
        self.env["OVERVIEW_TEST_GH_RC"] = str(rc)
        self.shim("gh", """
record('gh', sys.argv[1:])
record('gh-env', [os.environ.get('GH_PROMPT_DISABLED')])
if os.environ.get('OVERVIEW_TEST_TIMEOUT') == 'gh':
    import time
    record('timeout-pid', [os.getpid()])
    time.sleep(30)
if 'OVERVIEW_TEST_GH_RAW' in os.environ:
    print(os.environ['OVERVIEW_TEST_GH_RAW'])
    sys.exit(0)
if int(os.environ['OVERVIEW_TEST_GH_RC']):
    sys.exit(int(os.environ['OVERVIEW_TEST_GH_RC']))
prs = json.loads(os.environ['OVERVIEW_TEST_PRS'])
print(json.dumps(prs))
""")

    def snapshot(self, root):
        """File bytes, modes, mtimes, and symlink targets; reading changes atime."""
        result = {}
        for path in sorted(root.rglob("*")):
            info = path.lstat()
            if path.is_symlink():
                value = ("link", os.readlink(path))
            elif path.is_file():
                value = ("file", path.read_bytes())
            else:
                value = ("directory",)
            result[str(path.relative_to(root))] = (stat.S_IMODE(info.st_mode),
                                                    info.st_mtime_ns, value)
        return result

    def test_clean_repository_sections_and_head(self):
        output = self.output("--offline")
        positions = [output.index(name + " ─") for name in SECTIONS]
        self.assertEqual(positions, sorted(positions))
        head = self.section(output, "HEAD")
        for text in ("main", "initial subject", "1h ago", "no remote configured",
                     "no rebase/merge/bisect in progress"):
            self.assertIn(text, head)
        self.assertEqual(self.section(output, "UNCOMMITTED").strip(), "clean")
        for name in ("STASHES", "TAGS"):
            self.assertEqual(self.section(output, name).strip(), "(none)")
        self.assertIn("just this one", self.section(output, "WORKTREES"))
        self.assertIn("commit (initial): initial subject", self.section(output, "RECENT (reflog)"))

    def test_unborn_repository(self):
        empty = self.base / "empty"
        self.git("init", "-q", "-b", "new", str(empty))
        output = self.output("--offline", cwd=empty)
        self.assertIn("no commits yet", self.section(output, "HEAD"))
        self.assertIn("new", self.section(output, "HEAD"))
        self.assertIn("(none)", self.section(output, "BRANCHES"))
        self.assertIn("clean", self.section(output, "UNCOMMITTED"))

    def test_detached_head(self):
        self.git("checkout", "-q", "--detach")
        head = self.section(self.output("--offline"), "HEAD")
        self.assertIn("DETACHED HEAD", head)
        self.assertIn("n/a while detached", head)

    def test_staged_unstaged_and_unusual_untracked_names(self):
        (self.repo / "file").write_text("staged\n")
        self.git("add", "file")
        (self.repo / "file").write_text("modified again\n")
        for name in ("space name", "line\nbreak", 'quote"name', "café"):
            (self.repo / name).write_text("untracked")
        status = self.section(self.output("--offline"), "UNCOMMITTED")
        for text in ("1 staged", "1 modified", "4 untracked"):
            self.assertIn(text, status)

    def test_conflict_and_merge_operation(self):
        self.git("checkout", "-qb", "other")
        self.commit("other change", "other\n")
        self.git("checkout", "-q", "main")
        self.commit("main change", "main\n")
        result = self.git("merge", "other", check=False)
        self.assertEqual(result.returncode, 1)
        output = self.output("--offline")
        self.assertIn("MERGE in progress", self.section(output, "HEAD"))
        self.assertNotIn("no rebase/merge/bisect", self.section(output, "HEAD"))
        status = self.section(output, "UNCOMMITTED")
        self.assertIn("1 conflicted", status)
        self.assertIn("UU file", status)

    def test_operation_indicators(self):
        for marker, label in (("rebase-apply", "REBASE/AM"),
                              ("CHERRY_PICK_HEAD", "CHERRY-PICK"),
                              ("REVERT_HEAD", "REVERT"), ("BISECT_LOG", "BISECT")):
            with self.subTest(marker=marker):
                path = self.repo / ".git" / marker
                if marker == "rebase-apply":
                    path.mkdir()
                else:
                    path.write_text(self.root + "\n")
                try:
                    self.assertIn(label + " in progress",
                                  self.section(self.output("--offline"), "HEAD"))
                finally:
                    path.rmdir() if path.is_dir() else path.unlink()

    def test_branch_limit_order_and_current_branch_retention(self):
        for i in range(3):
            self.git("checkout", "-qB", f"topic{i}", self.root)
            self.commit(f"topic {i}", age=300 - i)
        self.git("checkout", "-q", "main")
        output = self.output("--offline", "-n", "1")
        branches = self.section(output, "BRANCHES")
        self.assertIn("topic2", branches)
        self.assertIn("* main", branches)
        self.assertNotIn("topic0", branches)
        self.assertNotIn("topic1", branches)
        self.assertIn("2 older branch(es) hidden", branches)
        self.assertLess(branches.index("topic2"), branches.index("* main"))
        for args in (("-n", "0"), ("--all-branches",)):
            with self.subTest(args=args):
                branches = self.section(self.output("--offline", *args), "BRANCHES")
                self.assertNotIn("hidden", branches)
                for name in ("main", "topic0", "topic1", "topic2"):
                    self.assertIn(name, branches)
        limited = self.section(self.output("--offline", env={"GIT_OVERVIEW_BRANCH_LIMIT": "1"}), "BRANCHES")
        self.assertIn("2 older branch(es) hidden", limited)

    def test_branch_name_is_not_shell_code(self):
        name = "$(touch${IFS}PWNED)`false`"
        self.git("branch", name)
        self.output("--offline", "--all-branches")
        self.assertFalse((self.repo / "PWNED").exists())

    def test_default_branch_limit_and_cli_override(self):
        for i in range(13):
            self.git("branch", f"topic{i:02}")
        branches = self.section(self.output("--offline"), "BRANCHES")
        self.assertIn("2 older branch(es) hidden", branches)
        branches = self.section(self.output("--offline", "--all-branches",
                                           env={"GIT_OVERVIEW_BRANCH_LIMIT": "1"}), "BRANCHES")
        self.assertNotIn("hidden", branches)
        for i in range(13):
            self.assertIn(f"topic{i:02}", branches)

    def test_pipe_in_branch_name_preserves_fields(self):
        self.git("branch", "topic|pipe")
        branches = self.section(self.output("--offline"), "BRANCHES")
        self.assertIn("topic|pipe", branches)
        self.assertRegex(branches, r"topic\|pipe\s+\(no up\)\s+1h ago\s+"
                         + self.root[:7] + r"\s+initial subject")

    def test_worktrees_markers_status_and_paths_with_spaces(self):
        linked = self.base / "linked worktree"
        self.git("worktree", "add", "-qb", "linked", str(linked))
        (linked / "file").write_text("changed\n")
        (linked / "new").write_text("untracked\n")
        self.git("worktree", "lock", str(linked))
        output = self.output("--offline")
        self.assertRegex(self.section(output, "BRANCHES"), r"(?m)^\+ linked\s")
        worktrees = self.section(output, "WORKTREES")
        self.assertIn("linked worktree", worktrees)
        self.assertIn("1 changed", worktrees)
        self.assertIn("1 untracked", worktrees)
        self.assertIn("[locked]", worktrees)
        self.assertRegex(worktrees, r"(?m)^\* .*repo\s")
        subdir = linked / "subdir"
        subdir.mkdir()
        linked_output = self.output("--offline", cwd=subdir)
        self.assertRegex(self.section(linked_output, "BRANCHES"), r"(?m)^\* linked\s")
        self.assertRegex(self.section(linked_output, "WORKTREES"), r"(?m)^\* .*linked worktree\s")

    def test_worktree_home_shortening_respects_directory_boundaries(self):
        linked = self.base / "repository" / "repo"
        self.git("worktree", "add", "-qb", "linked", str(linked))
        cases = (
            (str(self.repo), "~", str(linked)),
            (str(self.base), "~/repo", "~/repository/repo"),
            ("", str(self.repo), str(linked)),
        )
        for home, repo_path, linked_path in cases:
            with self.subTest(home=home):
                section = self.section(self.output("--offline", env={
                    "HOME": home, "COLUMNS": "200"}), "WORKTREES")
                self.assertRegex(section, r"(?m)^\* " + re.escape(repo_path) + r"\s")
                self.assertRegex(section, r"(?m)^  " + re.escape(linked_path) + r"\s")

    def test_missing_worktree_is_reported(self):
        linked = self.base / "missing"
        self.git("worktree", "add", "-qb", "missing", str(linked))
        linked.rename(self.base / "moved")
        section = self.section(self.output("--offline"), "WORKTREES")
        self.assertIn("path is missing", section)
        self.assertIn("[prunable]", section)

    def test_worktree_path_with_quote(self):
        linked = self.base / 'quoted"worktree'
        self.git("worktree", "add", "-qb", "quoted", str(linked))
        section = self.section(self.output("--offline"), "WORKTREES")
        self.assertNotIn("path is missing", section)
        self.assertIn('quoted"worktree', section)

    def test_worktree_path_with_newline(self):
        linked = self.base / "line\nbreak"
        self.git("worktree", "add", "-qb", "newline", str(linked))
        section = self.section(self.output("--offline"), "WORKTREES")
        self.assertNotIn("path is missing", section)
        self.assertRegex(section, r"newline\s+clean")
        self.assertIn(r"line\nbreak", section)
        active = self.section(self.output("--offline", cwd=linked), "WORKTREES")
        self.assertRegex(active, r"(?m)^\* .*line\\nbreak\s")

    def test_read_only_including_indexes_objects_refs_and_linked_worktrees(self):
        self.remote()
        linked = self.base / "linked"
        self.git("worktree", "add", "-qb", "linked", str(linked))
        for directory in (self.repo, linked):
            (directory / "file").write_text("staged\n")
            self.git("add", "file", cwd=directory)
            (directory / "file").write_text("unstaged\n")
            (directory / "untracked").write_text("untracked\n")
        # Configured fsmonitor must not execute during read-only status scans.
        hook = self.base / "fsmonitor"
        hook.write_text('#!/bin/sh\nprintf invoked > "$HOME/fsmonitor-invoked"\nexit 1\n')
        hook.chmod(0o755)
        self.git("config", "core.fsmonitor", str(hook))
        self.gh()
        before = [self.snapshot(root) for root in (self.repo, linked)]
        for args in (("--offline",), ()):
            self.output(*args, env={"OVERVIEW_TEST_REMOTE_RC": "0",
                                   "OVERVIEW_TEST_REMOTE": f"{self.root}\trefs/heads/main\n"})
            self.assertEqual(before, [self.snapshot(root) for root in (self.repo, linked)])
            self.assertFalse((self.base / "fsmonitor-invoked").exists())

    def test_offline_skips_all_network_commands(self):
        self.remote()
        self.gh()
        output = self.output("--offline")
        self.assertIn("skipped: --offline", self.section(output, "OPEN PRS"))
        self.assertIn("fetched 2d ago", self.section(output, "HEAD"))
        for tool, args in self.recorded():
            self.assertNotEqual(tool, "gh")
            self.assertFalse(set(args) & {"ls-remote", "fetch", "push", "clone"})

    def test_no_pr_still_checks_remote(self):
        self.remote()
        self.gh()
        output = self.output("--no-pr", env={"OVERVIEW_TEST_REMOTE_RC": "0",
                                             "OVERVIEW_TEST_REMOTE": f"{self.root}\trefs/heads/main\n"})
        self.assertIn("checked just now", self.section(output, "HEAD"))
        self.assertIn("skipped: --no-pr", self.section(output, "OPEN PRS"))
        self.assertTrue(any(tool == "git" and "ls-remote" in args for tool, args in self.recorded()))
        self.assertFalse(any(tool == "gh" for tool, _ in self.recorded()))

    def test_remote_freshness_states(self):
        self.remote()
        cases = ((self.root, "0", "checked just now"),
                 ("1" * 40, "0", "counts stale"),
                 ("", "0", "no longer exists on origin"),
                 ("", "128", "remote unreachable"))
        for oid, rc, expected in cases:
            with self.subTest(expected=expected):
                response = f"{oid}\trefs/heads/main\n" if oid else ""
                head = self.section(self.output("--no-pr", env={
                    "OVERVIEW_TEST_REMOTE": response, "OVERVIEW_TEST_REMOTE_RC": rc}), "HEAD")
                self.assertIn("in sync", head)
                self.assertIn(expected, head)

    def test_missing_tracking_ref_distinguishes_deleted_and_unfetched(self):
        self.remote(tracking=False)
        for response, expected in (("", "deleted on the remote"),
                                   (f"{self.root}\trefs/heads/main\n", "fetch to compare")):
            with self.subTest(expected=expected):
                head = self.section(self.output("--no-pr", env={
                    "OVERVIEW_TEST_REMOTE": response, "OVERVIEW_TEST_REMOTE_RC": "0"}), "HEAD")
                self.assertIn("no remote-tracking ref", head)
                self.assertIn(expected, head)
                self.assertNotIn("in sync", head)
        head = self.section(self.output("--offline"), "HEAD")
        self.assertIn("deleted upstream, or never fetched here", head)

    def test_remote_timeout_is_not_confirmation(self):
        self.remote()
        start = time.monotonic()
        head = self.section(self.output("--no-pr", env={"OVERVIEW_TEST_TIMEOUT": "git"}), "HEAD")
        self.assert_timeout(start, 5)
        self.assertIn("remote unreachable", head)
        self.assertNotIn("checked just now", head)
        self.assertTrue(any(tool == "git" and "ls-remote" in args
                            for tool, args in self.recorded()))

    def test_ahead_behind_counts_and_broken_upstream(self):
        self.remote()
        self.git("checkout", "-qb", "remote-side")
        self.commit("remote commit")
        self.git("update-ref", "refs/remotes/origin/main", self.oid())
        self.git("checkout", "-q", "main")
        self.commit("local commit")
        self.git("branch", "broken")
        self.git("config", "branch.broken.remote", "missing")
        self.git("config", "branch.broken.merge", "refs/heads/target")
        output = self.output("--offline")
        head = self.section(output, "HEAD")
        self.assertIn("↑1 ahead", head)
        self.assertIn("↓1 behind", head)
        branches = self.section(output, "BRANCHES")
        self.assertRegex(branches, r"main\s+↑1 ↓1")
        self.assertRegex(branches, r"broken\s+⚠ remote")
        self.assertIn("→missing/target", branches)

    def test_stash_tag_and_reflog_content(self):
        (self.repo / "file").write_text("stash me\n")
        self.git("stash", "push", "-qm", "saved | work")
        for i in range(7):
            self.git("tag", "-am", f"tag {i}", f"v{i}",
                     env={"GIT_COMMITTER_DATE": f"{NOW - 100 + i} +0000"})
        output = self.output("--offline")
        stashes = self.section(output, "STASHES")
        self.assertIn("stash@{0}", stashes)
        self.assertIn("saved | work", stashes)
        tags = self.section(output, "TAGS")
        self.assertIn("2 more", tags)
        self.assertLess(tags.index("v6"), tags.index("v5"))
        self.assertNotIn("v0", tags)
        self.assertIn("initial subject", self.section(output, "RECENT (reflog)"))

    def test_pr_states_and_title_delimiter(self):
        self.remote()
        prs = [dict(number=i + 1, title=f"title | {i}", headRefName=f"pr-{i}",
                    isDraft=draft, reviewDecision=review)
               for i, (draft, review) in enumerate(((True, ""), (False, ""),
                                                     (False, "APPROVED"), (False, "CHANGES_REQUESTED")))]
        self.gh(prs)
        section = self.section(self.output(), "OPEN PRS")
        for i, label in enumerate(("draft", "ready", "approved", "changes req")):
            self.assertRegex(section, rf"#{i + 1}\s+{label}\s+pr-{i}\s+title \| {i}")

    def test_pr_empty_failed_missing_cli_and_timeout_are_distinct(self):
        self.remote()
        self.assertIn("gh not installed", self.section(self.output(), "OPEN PRS"))
        self.gh()
        self.assertEqual(self.section(self.output(), "OPEN PRS").strip(), "(none)")
        self.gh(rc=1)
        self.assertIn("gh failed", self.section(self.output(), "OPEN PRS"))
        self.gh()
        start = time.monotonic()
        self.assertIn("timed out after 6s", self.section(self.output(
            env={"OVERVIEW_TEST_TIMEOUT": "gh"}), "OPEN PRS"))
        self.assert_timeout(start, 6)

    def test_no_remote_does_not_query_github(self):
        self.gh()
        self.assertIn("no remote configured", self.section(self.output(), "OPEN PRS"))
        self.assertFalse(any(tool == "gh" for tool, _ in self.recorded()))

    def test_query_failures_are_not_reported_as_empty_or_clean(self):
        for query, section in ((["status"], "UNCOMMITTED"),
                               (["for-each-ref", "refs/heads"], "BRANCHES"),
                               (["stash", "list"], "STASHES"),
                               (["for-each-ref", "refs/tags", "--count=5"], "TAGS"),
                               (["reflog"], "RECENT (reflog)")):
            with self.subTest(section=section):
                output = self.output("--offline", env={"OVERVIEW_TEST_FAIL": json.dumps(query)})
                content = self.section(output, section)
                self.assertIn("unavailable:", content)
                self.assertNotIn("(none)", content)
                self.assertNotIn("clean", content)

    def test_color_flags_and_no_color_environment(self):
        self.assertNotIn("\x1b[", self.output("--offline"))
        self.assertNotIn("\x1b[", self.output("--offline", "--no-color"))
        self.assertNotIn("\x1b[", self.output("--offline", "--color=auto", env={"NO_COLOR": "1"}))
        for flag in ("--color", "--color=always"):
            self.assertIn("\x1b[", self.output("--offline", flag, env={"NO_COLOR": "1"}))
        self.assertNotIn("\x1b[", self.output("--offline", "--color", "--color=never"))

    def test_width_clipping_and_current_branch_tail(self):
        name = "feature/" + "long-prefix-" * 4 + "distinct-tail"
        self.git("checkout", "-qb", name)
        self.commit("subject " + "x" * 160)
        for width in (60, 80, 120):
            with self.subTest(width=width):
                output = self.output("--offline", env={"COLUMNS": str(width)})
                branches = self.section(output, "BRANCHES")
                self.assertIn("…", branches)
                self.assertIn("distinct-tail", branches)
                self.assertTrue(all(len(line) <= width for line in branches.splitlines()), branches)
                header = next(line for line in output.splitlines() if line.lstrip().startswith("HEAD ─"))
                self.assertLessEqual(len(header), width)


    def test_renames_are_one_status_entry_even_with_status_like_names(self):
        linked = self.base / "linked"
        self.git("worktree", "add", "-qb", "linked", str(linked))
        for directory in (self.repo, linked):
            self.git("mv", "file", "?? renamed\nfile", cwd=directory)
            (directory / "?? renamed\nfile").write_text("modified again\n")
        output = self.output("--offline")
        status = self.section(output, "UNCOMMITTED")
        self.assertIn("1 staged", status)
        self.assertIn("1 modified", status)
        self.assertNotIn("untracked", status)
        worktrees = self.section(output, "WORKTREES")
        self.assertEqual(worktrees.count("1 changed"), 2)
        self.assertNotIn("untracked", worktrees)

    def test_worktree_trailing_newline_and_porcelain_like_lock_reason(self):
        linked = self.base / "trailing\n"
        self.git("worktree", "add", "-qb", "linked", str(linked))
        self.git("worktree", "lock", "--reason", "reason\nbranch refs/heads/fake", str(linked))
        worktrees = self.section(self.output("--offline", cwd=linked), "WORKTREES")
        self.assertRegex(worktrees, r"(?m)^\* .*trailing\\n\s")
        self.assertRegex(worktrees, r"linked\s+clean\s+\[locked\]")
        self.assertNotIn("fake", worktrees)
        self.assertNotIn("path is missing", worktrees)

    def test_non_utf8_worktree_path_is_used_without_loss(self):
        linked = self.base / os.fsdecode(b"invalid-\xff")
        self.git("worktree", "add", "-qb", "linked", str(linked))
        worktrees = self.section(self.output("--offline", cwd=linked), "WORKTREES")
        self.assertNotIn("path is missing", worktrees)
        self.assertRegex(worktrees, r"linked\s+clean")
        self.assertIn(r"invalid-\xff", worktrees)

    def test_pr_json_errors_are_not_empty_and_fields_keep_delimiters(self):
        self.remote()
        self.gh([dict(number=7, title='title | "quote"\nnext', headRefName="topic|pipe",
                      isDraft=False, reviewDecision="")])
        section = self.section(self.output(), "OPEN PRS")
        self.assertIn("topic|pipe", section)
        self.assertIn(r'title | "quote"\nnext', section)
        for response in ("", "not json", "null", "{}", '[{"number": 1}]'):
            with self.subTest(response=response):
                section = self.section(self.output(env={"OVERVIEW_TEST_GH_RAW": response}), "OPEN PRS")
                self.assertIn("gh failed", section)
                self.assertNotIn("(none)", section)
        self.gh([dict(number=8, title="no review", headRefName="unreviewed",
                      isDraft=False, reviewDecision=None)])
        self.assertRegex(self.section(self.output(), "OPEN PRS"), r"#8\s+ready\s+unreviewed")
        gh_args = [args for tool, args in self.recorded() if tool == "gh"]
        self.assertTrue(all("--json" in args and "--template" not in args for args in gh_args))

    def test_tag_delimiters_and_objects_without_dates(self):
        self.git("tag", "blob|tag", self.git("rev-parse", "HEAD:file").strip())
        self.git("tag", "tree|tag", "HEAD^{tree}")
        self.git("tag", "commit|tag")
        tags = self.section(self.output("--offline"), "TAGS")
        for name in ("blob|tag", "tree|tag", "commit|tag"):
            self.assertIn(name, tags)
        self.assertRegex(tags, r"blob\|tag\s+—")
        self.assertRegex(tags, r"tree\|tag\s+—")
        self.assertRegex(tags, r"commit\|tag\s+\d{4}-\d{2}-\d{2} 1h ago")

    def test_network_authentication_is_noninteractive(self):
        self.remote()
        self.gh()
        self.output(env={"GIT_TERMINAL_PROMPT": "1", "GIT_SSH_COMMAND": "custom-ssh -v",
                         "GCM_INTERACTIVE": "always", "GH_PROMPT_DISABLED": "0"})
        self.assertIn(["remote-env", ["0", "custom-ssh -v -o BatchMode=yes", "never"]], self.recorded())
        self.assertIn(["gh-env", ["1"]], self.recorded())

    def test_worktree_query_and_linked_status_failures(self):
        linked = self.base / "linked"
        self.git("worktree", "add", "-qb", "linked", str(linked))
        output = self.output("--offline", env={"OVERVIEW_TEST_FAIL": json.dumps(["-C", str(linked), "status"])})
        self.assertRegex(self.section(output, "WORKTREES"), r"linked\s+\(status failed\)")
        self.assertEqual(self.section(output, "UNCOMMITTED").strip(), "clean")
        output = self.output("--offline", env={"OVERVIEW_TEST_FAIL": json.dumps(["worktree", "list"])})
        self.assertIn("unavailable:", self.section(output, "WORKTREES"))
        self.assertNotIn("just this one", self.section(output, "WORKTREES"))

    def test_upstream_counts_failure_and_old_fetch_remain_explicit(self):
        self.remote()
        fetch = self.repo / ".git" / "FETCH_HEAD"
        os.utime(fetch, (NOW - 9 * 86400, NOW - 9 * 86400))
        head = self.section(self.output("--offline", env={
            "OVERVIEW_TEST_FAIL": json.dumps(["rev-list"])}), "HEAD")
        self.assertIn("ahead/behind unavailable", head)
        self.assertIn("fetched 9d ago — stale", head)
        self.assertNotIn("in sync", head)

    def test_width_clamps_without_terminal_utilities(self):
        for requested, expected in (("30", 60), ("200", 120), ("bad", 80)):
            with self.subTest(requested=requested):
                output = self.output("--offline", env={"COLUMNS": requested})
                header = next(line for line in output.splitlines() if line.lstrip().startswith("HEAD ─"))
                self.assertEqual(len(header), expected - 2)

    def test_help_and_invalid_options_outside_repository(self):
        help_result = self.run_overview("--help", cwd=self.base)
        self.assertIn(b"Usage: git-overview", help_result.stdout)
        for flag in ("--unknown", "--color=invalid"):
            with self.subTest(flag=flag):
                result = self.run_overview(flag, cwd=self.base, ok=False)
                self.assertEqual(result.returncode, 2)
                self.assertIn(b"git-overview:", result.stderr)
        result = self.run_overview(cwd=self.base, ok=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"not inside a git repository", result.stderr)


if __name__ == "__main__":
    unittest.main()
