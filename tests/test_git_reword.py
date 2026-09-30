# Run from the project root: python3 -m unittest discover -s tests -v
# Requires Python 3.8+ and Git 2.46+. Tests use isolated temporary repositories
# with user/system Git configuration disabled. SSH signing tests additionally
# require ssh-keygen and skip when it is unavailable.
import os
from pathlib import Path
import pty
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "git-reword"
REAL_GIT = shutil.which("git")
EDIT = 'printf "reworded\\n" > "$1"'


class RewordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="test-git-reword-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
        self.env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
                        GIT_AUTHOR_DATE="1700000000 +0000", GIT_COMMITTER_DATE="1800000000 +0000",
                        LC_ALL="C", HOME=str(self.base))
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "Reword Tester")
        self.git("config", "user.email", "test@example.com")
        self.commit("first", "one\n")
        self.root = self.oid()
        self.commit("second", "two\n")
        self.tip = self.oid()

    def git(self, *args, data=None, check=True, cwd=None):
        result = subprocess.run([REAL_GIT, *args], cwd=cwd or self.repo, env=self.env,
                                input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout if check else result

    def oid(self, ref="HEAD"):
        return self.git("rev-parse", ref).strip().decode()

    def commit(self, message, contents=None):
        if contents is not None:
            (self.repo / "file").write_text(contents)
            self.git("add", "file")
        self.git("commit", "--allow-empty", "-qm", message)
        return self.oid()

    def raw(self, ref="HEAD"):
        return self.git("cat-file", "commit", ref)

    def message(self, ref="HEAD"):
        return self.raw(ref).partition(b"\n\n")[2]

    def reword(self, target="HEAD~1", editor=EDIT, force=True, extraenv=None, cwd=None,
               terminal_answer=None):
        editor_path = self.base / "editor with spaces.sh"
        editor_path.write_text("#!/bin/sh\nset -eu\n" + editor + "\n")
        editor_path.chmod(0o755)
        env = dict(self.env, GIT_EDITOR=shlex.quote(str(editor_path)))
        env.update(extraenv or {})
        command = [str(SCRIPT), *(["-f"] if force else []), target]
        options = dict(cwd=cwd or self.repo, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, timeout=20)
        if terminal_answer is None:
            return subprocess.run(command, input=b"", **options)
        master, slave = pty.openpty()
        try:
            os.write(master, terminal_answer.encode())
            return subprocess.run(command, stdin=slave, **options)
        finally:
            os.close(master)
            os.close(slave)

    def succeeds(self, result):
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))

    def fails(self, result, contains):
        self.assertNotEqual(result.returncode, 0, result.stdout.decode(errors="replace"))
        self.assertIn(contains.encode(), result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)

    def inject_git(self, code):
        directory = self.base / "bin"
        directory.mkdir(exist_ok=True)
        wrapper = directory / "git"
        wrapper.write_text(f"#!{sys.executable}\nimport os, sys\nfrom pathlib import Path\n"
                           + code + f"\nos.execv({REAL_GIT!r}, [{REAL_GIT!r}, *sys.argv[1:]])\n")
        wrapper.chmod(0o755)
        return {"PATH": str(directory) + os.pathsep + self.env["PATH"]}

    def install_raw(self, raw):
        oid = self.git("hash-object", "-t", "commit", "-w", "--stdin", data=raw).strip().decode()
        self.git("update-ref", "HEAD", oid)
        return oid

    def add_note(self, oid, contents=b"a note\n", ref="refs/notes/commits"):
        blob = self.git("hash-object", "-w", "--stdin", data=contents).strip().decode()
        self.git("notes", "--ref=" + ref, "add", "--allow-empty", "-C", blob, oid)
        return self.git("notes", "--ref=" + ref, "list", oid).strip()

    def ssh_signing(self):
        if not shutil.which("ssh-keygen"):
            self.skipTest("ssh-keygen unavailable")
        key = self.base / "signing-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        allowed = self.base / "allowed-signers"
        allowed.write_text("test@example.com " + key.with_suffix(".pub").read_text())
        self.git("config", "gpg.format", "ssh")
        self.git("config", "user.signingKey", str(key))
        self.git("config", "gpg.ssh.allowedSignersFile", str(allowed))
        self.git("config", "commit.gpgSign", "true")

    def test_root_and_descendant_preservation(self):
        before = [self.raw(self.root), self.raw(self.tip)]
        self.succeeds(self.reword())
        after = [self.raw("HEAD~1"), self.raw()]
        for old, new in zip(before, after):
            for field in (b"tree", b"author", b"committer"):
                pattern = rb"(?m)^" + field + rb" .*"
                self.assertEqual(re.search(pattern, old)[0], re.search(pattern, new)[0])
        self.assertEqual(self.message("HEAD~1"), b"reworded\n")
        self.assertEqual(self.message(), b"second\n")
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), b"2")
        self.git("fsck", "--no-reflogs")

    def test_dates_timezones_and_identities_survive_environment_overrides(self):
        raw = self.raw()
        raw = re.sub(rb"(?m)^author .*", b"author Original Author <author@example.com> 1600000000 +0545", raw)
        raw = re.sub(rb"(?m)^committer .*", b"committer Original Committer <committer@example.com> 1650000000 -0330", raw)
        self.install_raw(raw)
        before = [self.raw("HEAD~1"), self.raw()]
        overrides = dict(GIT_AUTHOR_NAME="Someone Else", GIT_AUTHOR_EMAIL="else@example.com",
                         GIT_AUTHOR_DATE="1900000000 +0100", GIT_COMMITTER_NAME="New User",
                         GIT_COMMITTER_EMAIL="new@example.com", GIT_COMMITTER_DATE="1950000000 -0700")
        self.succeeds(self.reword(extraenv=overrides))
        for original, rewritten in zip(before, [self.raw("HEAD~1"), self.raw()]):
            for key in (b"author", b"committer"):
                pattern = rb"(?m)^" + key + rb" .*"
                self.assertEqual(re.search(pattern, original)[0], re.search(pattern, rewritten)[0])

    def test_notes_copied_from_every_namespace_for_each_rewritten_commit(self):
        refs = ("refs/notes/commits", "refs/notes/review")
        expected = {}
        for ref in refs:
            expected[ref] = (self.add_note(self.root, b"root note\x00\n", ref),
                             self.add_note(self.tip, b"descendant note\n", ref))
        unrelated = self.oid("HEAD^{tree}")
        untouched = self.add_note(unrelated, b"tree note\n")
        # Explicit retention includes notes hidden by display/rewrite preferences.
        self.git("config", "notes.rewrite.rebase", "false")
        self.git("config", "notes.rewriteRef", "refs/notes/commits")
        self.succeeds(self.reword())
        for ref in refs:
            for source, replacement, note in zip((self.root, self.tip), ("HEAD~1", "HEAD"), expected[ref]):
                self.assertEqual(self.git("notes", "--ref=" + ref, "list", replacement).strip(), note)
                self.assertEqual(self.git("notes", "--ref=" + ref, "list", source).strip(), note)
        self.assertEqual(self.git("notes", "list", unrelated).strip(), untouched)
        self.assertEqual(list((self.repo / ".git").glob("reword-*")), [])
        self.git("fsck")

    def test_reword_head_copies_note_without_rewriting_other_notes(self):
        root_note = self.add_note(self.root)
        head_note = self.add_note(self.tip, b"head note\n")
        self.succeeds(self.reword("HEAD"))
        self.assertEqual(self.git("notes", "list", "HEAD").strip(), head_note)
        self.assertEqual(self.git("notes", "list", self.root).strip(), root_note)
        self.assertEqual(self.oid("HEAD^"), self.root)

    def test_notes_in_fanout_tree_and_unrelated_entries_survive(self):
        note = self.git("hash-object", "-w", "--stdin", data=b"fanout note\n").strip()
        leaf = self.git("mktree", data=b"100644 blob " + note + b"\t" + self.tip[4:].encode() + b"\n").strip()
        middle = self.git("mktree", data=b"040000 tree " + leaf + b"\t" + self.tip[2:4].encode() + b"\n").strip()
        tree = self.git("mktree", data=b"040000 tree " + middle + b"\t" + self.tip[:2].encode()
                        + b"\n100644 blob " + note + b"\tREADME\n").strip()
        old = self.git("commit-tree", tree.decode(), "-m", "notes fixture").strip().decode()
        self.git("update-ref", "refs/notes/fanout", old)
        self.succeeds(self.reword())
        self.assertEqual(self.git("notes", "--ref=fanout", "list", "HEAD").strip(), note)
        self.assertEqual(self.git("notes", "--ref=fanout", "list", self.tip).strip(), note)
        self.assertEqual(self.git("cat-file", "blob", "refs/notes/fanout:README"), b"fanout note\n")
        self.assertEqual(self.oid("refs/notes/fanout^"), old)

    def test_concurrent_notes_edit_aborts_without_overwriting_it(self):
        self.add_note(self.tip)
        editor = 'git notes add -f -m "concurrent note" HEAD\n' + EDIT
        self.fails(self.reword(editor=editor), "ref transaction refused")
        self.assertEqual(self.oid(), self.tip)
        self.assertEqual(self.git("notes", "show", "HEAD"), b"concurrent note\n")

    def test_new_notes_namespace_during_edit_aborts(self):
        editor = 'git notes --ref=review add -m "concurrent note" HEAD\n' + EDIT
        self.fails(self.reword(editor=editor), "notes changed")
        self.assertEqual(self.oid(), self.tip)
        self.assertEqual(self.git("notes", "--ref=review", "show", "HEAD"), b"concurrent note\n")

    def test_failed_note_copy_does_not_move_real_refs(self):
        self.add_note(self.tip)
        old_notes = self.oid("refs/notes/commits")
        env = self.inject_git('if "notes" in sys.argv and "copy" in sys.argv:\n    sys.exit(128)')
        self.fails(self.reword(extraenv=env), "failed (128)")
        self.assertEqual(self.oid(), self.tip)
        self.assertEqual(self.oid("refs/notes/commits"), old_notes)
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)", "refs/notes"), b"refs/notes/commits\n")

    def test_rejected_transaction_keeps_branch_and_notes_together(self):
        self.add_note(self.tip)
        old_notes = self.oid("refs/notes/commits")
        hook = self.repo / ".git/hooks/reference-transaction"
        hook.write_text('#!/bin/sh\n[ "$1" != prepared ]\n')
        hook.chmod(0o755)
        self.fails(self.reword(), "ref transaction refused")
        self.assertEqual(self.oid(), self.tip)
        self.assertEqual(self.oid("refs/notes/commits"), old_notes)

    def test_existing_identical_destination_note_is_retained(self):
        note = self.add_note(self.tip)
        self.succeeds(self.reword())
        rewritten = self.oid()
        notes_tip = self.oid("refs/notes/commits")
        self.git("update-ref", "HEAD", self.tip)
        self.succeeds(self.reword())
        self.assertEqual(self.oid(), rewritten)
        self.assertEqual(self.oid("refs/notes/commits"), notes_tip)
        self.assertEqual(self.git("notes", "list", "HEAD").strip(), note)

    def test_conflicting_destination_note_aborts(self):
        self.add_note(self.tip)
        self.succeeds(self.reword())
        rewritten = self.oid()
        self.git("notes", "add", "-f", "-m", "different note", rewritten)
        notes_tip = self.oid("refs/notes/commits")
        self.git("update-ref", "HEAD", self.tip)
        self.fails(self.reword(), "different notes already exist")
        self.assertEqual(self.oid(), self.tip)
        self.assertEqual(self.oid("refs/notes/commits"), notes_tip)
        self.assertEqual(self.git("notes", "show", rewritten), b"different note\n")

    def test_empty_head(self):
        self.commit("empty")
        tree, parent = self.oid("HEAD^{tree}"), self.oid("HEAD^")
        self.succeeds(self.reword("HEAD"))
        self.assertEqual(self.oid("HEAD^{tree}"), tree)
        self.assertEqual(self.oid("HEAD^"), parent)
        self.assertEqual(self.message(), b"reworded\n")

    def test_only_commit_root(self):
        self.git("checkout", "--detach", self.root)
        self.succeeds(self.reword("HEAD"))
        self.assertEqual(self.git("rev-list", "--count", "HEAD").strip(), b"1")
        self.assertEqual(self.message(), b"reworded\n")

    def test_merge_and_unrelated_parent_with_dirty_index(self):
        self.git("checkout", "-qb", "side", self.root)
        (self.repo / "side").write_text("side\n")
        self.git("add", "side")
        side = self.commit("side")
        self.git("checkout", "-q", "main")
        self.git("merge", "--no-ff", "-qm", "merge", "side")
        merge = self.oid()
        (self.repo / "file").write_text("staged\n")
        self.git("add", "file")
        (self.repo / "file").write_text("unstaged\n")
        (self.repo / "untracked").write_bytes(b"untracked\x00")
        editmsg = self.repo / ".git/COMMIT_EDITMSG"
        editmsg.write_bytes(b"do not overwrite me")
        index = (self.repo / ".git/index").read_bytes()
        self.succeeds(self.reword(self.tip))
        self.assertEqual(self.oid("HEAD^2"), side)
        self.assertEqual(self.oid("side"), side)
        self.assertEqual(self.oid("HEAD^{tree}"), self.oid(merge + "^{tree}"))
        self.assertEqual(self.oid("HEAD^1^{tree}"), self.oid(self.tip + "^{tree}"))
        self.assertEqual(self.message("HEAD^1"), b"reworded\n")
        self.assertEqual((self.repo / ".git/index").read_bytes(), index)
        self.assertEqual((self.repo / "file").read_bytes(), b"unstaged\n")
        self.assertEqual((self.repo / "untracked").read_bytes(), b"untracked\x00")
        self.assertEqual(editmsg.read_bytes(), b"do not overwrite me")
        self.assertIn(b"reword ", self.git("reflog", "-1", "--format=%gs", "HEAD"))

    def test_root_rewrite_preserves_dates_and_notes_on_both_merge_parents(self):
        self.git("checkout", "-qb", "side", self.root)
        self.env.update(GIT_AUTHOR_DATE="1750000000 +0545", GIT_COMMITTER_DATE="1850000000 -0330")
        side = self.commit("side")
        self.git("checkout", "-q", "main")
        self.git("merge", "--no-ff", "-qm", "merge side", "side")
        originals = [self.root, self.tip, side, self.oid()]
        notes = [self.add_note(oid, b"" if oid == side else oid.encode()) for oid in originals]
        self.succeeds(self.reword(self.root, extraenv={"GIT_COMMITTER_DATE": "1950000000 +0000"}))
        for original, replacement, note in zip(originals, ("HEAD^1^", "HEAD^1", "HEAD^2", "HEAD"), notes):
            for key in (b"tree", b"author", b"committer"):
                pattern = rb"(?m)^" + key + rb" .*"
                self.assertEqual(re.search(pattern, self.raw(original))[0],
                                 re.search(pattern, self.raw(replacement))[0])
            self.assertEqual(self.git("notes", "list", replacement).strip(), note)
            self.assertEqual(self.git("notes", "list", original).strip(), note)
        self.assertEqual(self.git("notes", "show", "HEAD^2"), b"")
        self.assertEqual(self.oid("side"), side)

    def test_noop_preserves_comments_whitespace_and_encoding(self):
        raw = self.raw().partition(b"\n\n")[0] + b"\nencoding ISO-8859-1\n\n# caf\xe9\r\n\nbody  \n\n"
        old = self.install_raw(raw)
        self.git("config", "core.commentChar", "auto")
        self.git("config", "commit.cleanup", "strip")
        self.succeeds(self.reword("HEAD", editor=":"))
        self.assertEqual(self.oid(), old)
        self.assertEqual(self.raw(), raw)

    def test_edited_message_is_verbatim(self):
        self.git("config", "commit.cleanup", "verbatim")
        self.succeeds(self.reword("HEAD", editor='printf "# title\\n\\nbody  \\n\\n" > "$1"'))
        self.assertEqual(self.message(), b"# title\n\nbody  \n\n")

    def test_default_cleanup_strips_comments_and_whitespace(self):
        self.succeeds(self.reword("HEAD", editor='printf "# comment\\n\\nbody  \\n\\n\\nend\\t\\n" > "$1"'))
        self.assertEqual(self.message(), b"body\n\nend\n")

    def test_whitespace_cleanup_keeps_comment_lines(self):
        self.git("config", "commit.cleanup", "whitespace")
        self.succeeds(self.reword("HEAD", editor='printf "# title\\n\\nbody  \\n\\n" > "$1"'))
        self.assertEqual(self.message(), b"# title\n\nbody\n")

    def test_cleanup_uses_configured_comment_prefix(self):
        self.git("config", "core.commentChar", ";")
        self.succeeds(self.reword("HEAD", editor='printf "; comment\\n# title\\n" > "$1"'))
        self.assertEqual(self.message(), b"# title\n")

    def test_scissors_cleanup_uses_git_comment_prefix(self):
        self.git("config", "commit.cleanup", "scissors")
        self.git("config", "core.commentString", "//")
        editor = 'printf "# title\\nbody  \\n// ------------------------ >8 ------------------------\\nignore me\\n" > "$1"'
        self.succeeds(self.reword("HEAD", editor=editor))
        self.assertEqual(self.message(), b"# title\nbody\n")

    def test_comment_only_message_cancels_after_cleanup(self):
        self.succeeds(self.reword("HEAD", editor='printf "# just a comment\\n" > "$1"'))
        self.assertEqual(self.oid(), self.tip)

    def test_cleanup_yielding_original_message_does_not_rewrite(self):
        self.succeeds(self.reword("HEAD", editor='printf "# added comment\\n" >> "$1"'))
        self.assertEqual(self.oid(), self.tip)

    def test_invalid_cleanup_configuration_aborts(self):
        self.git("config", "commit.cleanup", "invalid")
        self.fails(self.reword(), "invalid commit.cleanup")
        self.assertEqual(self.oid(), self.tip)

    def test_empty_message_cancels(self):
        self.succeeds(self.reword(editor='printf " \\n\\t" > "$1"'))
        self.assertEqual(self.oid(), self.tip)

    def test_failed_editor_preserves_edit(self):
        self.fails(self.reword(editor=EDIT + "\nexit 3"), "editor failed")
        self.assertEqual(self.oid(), self.tip)
        saved = list((self.repo / ".git").glob("reword-*/COMMIT_EDITMSG"))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].read_bytes(), b"reworded\n")

    def test_metadata_and_descendant_message_bytes_preserved(self):
        headers = self.raw().partition(b"\n\n")[0]
        raw = headers + b"\nencoding ISO-8859-1\nx-extra value\n continued\n\n# caf\xe9  \n\n"
        self.install_raw(raw)
        self.succeeds(self.reword())
        new = self.raw()
        normalize = lambda b: re.sub(rb"(?m)^(parent|committer) .*\n", b"", b)
        self.assertEqual(normalize(new), normalize(raw))

    def test_concurrent_commit_survives(self):
        editor = 'printf "concurrent\\n" > file\ngit add file\ngit commit -qm concurrent\n' + EDIT
        self.fails(self.reword(editor=editor), "ref transaction refused")
        self.assertEqual(self.message(), b"concurrent\n")
        self.assertEqual(self.oid("HEAD^"), self.tip)
        self.assertEqual(self.git("status", "--porcelain"), b"")

    def test_switch_to_branch_at_same_tip_is_rejected(self):
        self.git("branch", "other")
        self.fails(self.reword(editor="git checkout -q other\n" + EDIT), "identity changed")
        self.assertEqual(self.oid("main"), self.tip)
        self.assertEqual(self.oid("other"), self.tip)
        self.assertEqual(self.git("symbolic-ref", "HEAD").strip(), b"refs/heads/other")

    def test_detach_while_editing_is_rejected(self):
        self.fails(self.reword(editor="git checkout -q --detach\n" + EDIT), "identity changed")
        self.assertEqual(self.oid(), self.tip)
        self.assertEqual(self.oid("main"), self.tip)

    def test_attach_while_editing_is_rejected(self):
        self.git("checkout", "-q", "--detach")
        self.fails(self.reword(editor="git checkout -q main\n" + EDIT), "identity changed")
        self.assertEqual(self.oid(), self.tip)
        self.assertEqual(self.git("symbolic-ref", "HEAD").strip(), b"refs/heads/main")

    def test_detached_head(self):
        self.git("checkout", "-q", "--detach")
        self.succeeds(self.reword())
        self.assertEqual(self.oid("main"), self.tip)
        self.assertNotEqual(self.oid(), self.tip)
        self.assertEqual(self.git("symbolic-ref", "-q", "HEAD", check=False).returncode, 1)

    def test_both_ref_locks_are_held_before_publication(self):
        self.git("branch", "other")
        marker = self.base / "locks-verified"
        hook = self.repo / ".git/hooks/reference-transaction"
        hook.write_text('#!/bin/sh\nif [ "$1" = prepared ]; then\n'
                        '  if git symbolic-ref HEAD refs/heads/other 2>/dev/null; then exit 91; fi\n'
                        f'  if git update-ref refs/heads/main {self.root} {self.tip} 2>/dev/null; then exit 92; fi\n'
                        f'  touch {shlex.quote(str(marker))}\nfi\n')
        hook.chmod(0o755)
        self.succeeds(self.reword())
        self.assertTrue(marker.exists())
        self.assertEqual(self.git("symbolic-ref", "HEAD").strip(), b"refs/heads/main")

    def test_ref_hook_rejection_leaves_branch_unchanged(self):
        hook = self.repo / ".git/hooks/reference-transaction"
        hook.write_text('#!/bin/sh\n[ "$1" != prepared ]\n')
        hook.chmod(0o755)
        self.fails(self.reword(), "ref transaction refused")
        self.assertEqual(self.oid(), self.tip)

    def test_ref_hook_output_does_not_break_transaction_protocol(self):
        hook = self.repo / ".git/hooks/reference-transaction"
        hook.write_text('#!/bin/sh\nprintf "hook: %s\\n" "$1"\n')
        hook.chmod(0o755)
        result = self.reword()
        self.succeeds(result)
        self.assertIn(b"hook: prepared", result.stderr)
        self.assertIn(b"hook: committed", result.stderr)

    def test_commit_hooks_are_warned_about_and_not_run(self):
        hook = self.repo / ".git/hooks/pre-commit"
        hook.write_text('#!/bin/sh\nprintf "oops" > file\ngit add file\n')
        hook.chmod(0o755)
        self.fails(self.reword("HEAD", force=False), "confirmation needs a terminal")
        result = self.reword("HEAD")
        self.succeeds(result)
        self.assertIn(b"warning: commit/rebase hooks are not run: pre-commit", result.stderr)
        self.assertNotIn(b"Continue?", result.stderr)
        self.assertEqual((self.repo / "file").read_text(), "two\n")
        self.assertEqual(self.oid("HEAD^{tree}"), self.oid(self.tip + "^{tree}"))

    def test_hook_prompt_defaults_to_no_without_opening_editor(self):
        hook = self.repo / ".git/hooks/commit-msg"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        marker = self.base / "editor-was-opened"
        editor = "touch " + shlex.quote(str(marker)) + "\n" + EDIT
        for answer in ("\n", "n\n", "No\n", "yesterday\n"):
            with self.subTest(answer=answer):
                result = self.reword(force=False, editor=editor, terminal_answer=answer)
                self.fails(result, "aborted")
                self.assertIn(b"Continue? [y/N]", result.stderr)
                self.assertFalse(marker.exists())
                self.assertEqual(self.oid(), self.tip)

    def test_hook_prompt_accepts_yes_and_lists_all_skipped_hooks(self):
        names = ("pre-commit", "pre-rebase", "prepare-commit-msg", "commit-msg", "post-commit", "post-rewrite")
        marker = self.base / "hook-was-run"
        for name in names:
            hook = self.repo / ".git/hooks" / name
            hook.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\nexit 1\n")
            hook.chmod(0o755)
        result = self.reword(force=False, terminal_answer="yes\n")
        self.succeeds(result)
        self.assertEqual(result.stderr.count(b"Continue? [y/N]"), 1)
        for name in names:
            self.assertIn(name.encode(), result.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(self.message("HEAD~1"), b"reworded\n")

    def test_nonexecutable_hook_does_not_require_confirmation(self):
        hook = self.repo / ".git/hooks/commit-msg"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o644)
        result = self.reword(force=False)
        self.succeeds(result)
        self.assertNotIn(b"commit/rebase hooks", result.stderr)

    def test_disabled_hook_directory_does_not_require_confirmation(self):
        hook = self.repo / ".git/hooks/commit-msg"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.git("config", "core.hooksPath", os.devnull)
        result = self.reword(force=False)
        self.succeeds(result)
        self.assertNotIn(b"commit/rebase hooks", result.stderr)

    def test_configured_hooks_also_trigger_warning_and_confirmation(self):
        self.git("config", "hook.check.event", "pre-commit")
        self.git("config", "hook.check.command", "exit 1")
        self.fails(self.reword(force=False), "confirmation needs a terminal")
        result = self.reword()
        self.succeeds(result)
        self.assertIn(b"configured hooks found", result.stderr)
        self.assertNotIn(b"Continue?", result.stderr)

    def test_rev_list_failure_aborts_before_editor(self):
        env = self.inject_git('if sys.argv[1] == "rev-list":\n    sys.exit(128)')
        self.fails(self.reword(extraenv=env), "rev-list")
        self.assertEqual(self.oid(), self.tip)

    def test_partial_object_write_failure_does_not_publish(self):
        counter = self.base / "counter"
        env = self.inject_git(f'''if sys.argv[1] == "hash-object":
    p = Path({str(counter)!r})
    if p.exists(): sys.exit(128)
    p.touch()''')
        self.fails(self.reword(extraenv=env), "hash-object")
        self.assertEqual(self.oid(), self.tip)
        self.assertTrue(list((self.repo / ".git").glob("reword-*/COMMIT_EDITMSG")))

    def test_shallow_rejected_even_with_force(self):
        clone = self.base / "shallow"
        self.git("clone", "-q", "--depth=2", self.repo.as_uri(), str(clone))
        self.fails(self.reword(cwd=clone), "shallow history")
        self.assertEqual(self.git("rev-parse", "HEAD", cwd=clone).strip().decode(), self.tip)

    def test_replacement_refs_rejected(self):
        self.git("replace", self.tip, self.root)
        self.fails(self.reword(), "replacement refs")
        self.assertEqual(self.oid(), self.tip)

    def test_active_operations_rejected(self):
        for name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "rebase-merge", "BISECT_START"):
            with self.subTest(name=name):
                path = self.repo / ".git" / name
                path.write_text(self.root + "\n")
                self.fails(self.reword(), name)
                self.assertEqual(self.oid(), self.tip)
                path.unlink()

    def test_invalid_signing_config_is_not_treated_as_false(self):
        self.git("config", "commit.gpgSign", "garbage")
        self.fails(self.reword(), "bad boolean")
        self.assertEqual(self.oid(), self.tip)

    def test_remote_warning_requires_confirmation(self):
        self.git("update-ref", "refs/remotes/origin/main", self.tip)
        self.fails(self.reword(force=False), "confirmation needs a terminal")
        result = self.reword()
        self.succeeds(result)
        self.assertIn(b"local remote-tracking", result.stderr)
        self.assertEqual(self.oid("refs/remotes/origin/main"), self.tip)

    def test_unsigned_rewrite_warns_and_removes_old_signatures(self):
        headers, _, msg = self.raw().partition(b"\n\n")
        raw = headers + b"\ngpgsig -----BEGIN PGP SIGNATURE-----\n fake\n -----END PGP SIGNATURE-----\n\n" + msg
        old = self.install_raw(raw)
        self.fails(self.reword(force=False), "will lose their signatures")
        self.assertEqual(self.oid(), old)
        self.succeeds(self.reword())
        self.assertNotIn(b"\ngpgsig ", self.raw())

    def test_ssh_signing_all_rewritten_commits(self):
        self.ssh_signing()
        before = [self.raw("HEAD~1"), self.raw()]
        note = self.add_note(self.tip)
        self.succeeds(self.reword(extraenv={"GIT_COMMITTER_DATE": "1950000000 +0530"}))
        self.git("verify-commit", "HEAD")
        self.git("verify-commit", "HEAD~1")
        self.assertEqual(self.message(), b"second\n")
        self.assertEqual(self.git("notes", "list", "HEAD").strip(), note)
        for original, rewritten in zip(before, [self.raw("HEAD~1"), self.raw()]):
            for key in (b"author", b"committer"):
                pattern = rb"(?m)^" + key + rb" .*"
                self.assertEqual(re.search(pattern, original)[0], re.search(pattern, rewritten)[0])

    def test_signer_failure_never_publishes(self):
        self.git("config", "commit.gpgSign", "true")
        self.git("config", "gpg.program", "/bin/false")
        self.fails(self.reword(), "commit-tree")
        self.assertEqual(self.oid(), self.tip)

    def test_signing_cannot_silently_drop_extension_headers(self):
        headers, _, msg = self.raw().partition(b"\n\n")
        old = self.install_raw(headers + b"\nx-extra keep-me\n\n" + msg)
        self.ssh_signing()
        self.fails(self.reword("HEAD"), "could not preserve")
        self.assertEqual(self.oid(), old)
        self.assertIn(b"\nx-extra keep-me\n", self.raw())

    def test_signed_legacy_encoding(self):
        headers = self.raw().partition(b"\n\n")[0]
        self.install_raw(headers + b"\nencoding ISO-8859-1\n\ncaf\xe9\n")
        self.ssh_signing()
        self.succeeds(self.reword())
        self.git("verify-commit", "HEAD")
        self.assertEqual(self.message(), b"caf\xe9\n")
        self.assertIn(b"\nencoding ISO-8859-1\n", self.raw())

    def test_invalidated_mergetag_is_rejected(self):
        headers, _, msg = self.raw().partition(b"\n\n")
        tag = b"\nmergetag object " + self.root.encode() + b"\n type commit\n tag v1\n tagger T <t@t> 1700000000 +0000\n \n tag message"
        old = self.install_raw(headers + tag + b"\n\n" + msg)
        self.fails(self.reword(), "invalidate a mergetag")
        self.assertEqual(self.oid(), old)

    def test_worktree_from_subdirectory_with_relative_hooks_path(self):
        note = self.add_note(self.tip)
        worktree = self.base / "linked worktree"
        self.git("worktree", "add", "-q", "-b", "linked", str(worktree))
        sub = worktree / "sub"
        sub.mkdir()
        hooks = worktree / "hooks"
        hooks.mkdir()
        hook = hooks / "commit-msg"
        hook.write_text("#!/bin/sh\nexit 1\n")
        hook.chmod(0o755)
        self.git("config", "core.hooksPath", "hooks")
        result = self.reword(cwd=sub)
        self.succeeds(result)
        self.assertIn(b"commit-msg", result.stderr)
        self.assertEqual(self.oid("main"), self.tip)
        self.assertNotEqual(self.oid("linked"), self.tip)
        self.assertEqual(self.git("notes", "list", "linked").strip(), note)

    def test_sha256_repository(self):
        sha256 = self.base / "sha256"
        sha256.mkdir()
        result = self.git("init", "-q", "--object-format=sha256", "-b", "main", cwd=sha256, check=False)
        if result.returncode:
            self.skipTest("Git does not support SHA-256")
        self.repo = sha256
        self.git("config", "user.name", "Tester")
        self.git("config", "user.email", "test@example.com")
        self.commit("first", "one\n")
        self.commit("second", "two\n")
        note = self.add_note(self.oid())
        self.succeeds(self.reword())
        self.assertEqual(len(self.oid()), 64)
        self.assertEqual(self.message("HEAD~1"), b"reworded\n")
        self.assertEqual(self.git("notes", "list", "HEAD").strip(), note)
        self.git("fsck")

    def test_undo_is_conditional_and_names_original_branch(self):
        result = self.reword()
        self.succeeds(result)
        undo = shlex.split(result.stdout.decode().splitlines()[-1].strip())
        self.assertIn("refs/heads/main", undo)
        self.commit("later", "later\n")
        latest = self.oid()
        failed = subprocess.run(undo, cwd=self.repo, env=self.env, capture_output=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(self.oid(), latest)

    def test_nonancestor_rejected(self):
        self.git("checkout", "-qb", "side", self.root)
        side = self.commit("unrelated", "unrelated\n")
        self.git("checkout", "-q", "main")
        self.fails(self.reword(side), "not an ancestor")
        self.assertEqual(self.oid(), self.tip)

    def test_commitish_syntax_uses_git_revision_parser(self):
        self.git("checkout", "-qb", "side", self.root)
        side_commits = [self.commit("side-" + str(i)) for i in range(1, 5)]
        self.git("checkout", "-q", "main")
        self.git("merge", "--no-ff", "-qm", "merge side", "side")
        merge = self.oid()
        self.git("tag", "-a", "annotated", "-m", "tag", side_commits[0])
        self.git("config", "branch.main.remote", ".")
        self.git("config", "branch.main.merge", "refs/heads/side")
        cases = {
            "HEAD^2~3": side_commits[0], "main": merge, "@": merge,
            "side": side_commits[-1], "annotated": side_commits[0],
            self.root[:10]: self.root, "HEAD@{1}": self.tip,
            "@{-1}": side_commits[-1], "@{upstream}": side_commits[-1],
            "HEAD^{/side-1}": side_commits[0], ":/side-1": side_commits[0],
        }
        for expression, expected in cases.items():
            with self.subTest(expression=expression):
                result = self.reword(expression, editor=":")
                self.succeeds(result)
                self.assertIn(("Rewording " + expected[:12]).encode(), result.stderr)
                self.assertEqual(self.oid(), merge)

    def test_ranges_and_noncommits_are_rejected(self):
        for expression in ("HEAD~1..HEAD", "HEAD^@", "HEAD^{tree}", "HEAD:file"):
            with self.subTest(expression=expression):
                self.fails(self.reword(expression), "rev-parse")
                self.assertEqual(self.oid(), self.tip)

    def test_tag_and_branch_targets_only_rewrite_current_branch(self):
        self.git("tag", "-a", "inner", "-m", "inner tag", self.root)
        self.git("tag", "-a", "outer", "-m", "outer tag", "inner")
        self.git("branch", "saved", self.root)
        tag_ids = [self.oid(name) for name in ("inner", "outer")]
        for target in ("outer", "saved"):
            with self.subTest(target=target):
                self.git("update-ref", "HEAD", self.tip)
                self.succeeds(self.reword(target))
                self.assertEqual(self.message("HEAD~1"), b"reworded\n")
                self.assertEqual(self.message(), b"second\n")
                self.assertEqual(self.oid("saved"), self.root)
                self.assertEqual([self.oid(name) for name in ("inner", "outer")], tag_ids)

    def test_ancestry_expression_rewords_the_selected_merge_side_commit(self):
        self.git("checkout", "-qb", "side", self.root)
        old_side = [self.commit("side-" + str(i)) for i in range(1, 5)]
        self.git("checkout", "-q", "main")
        self.git("merge", "--no-ff", "-qm", "merge side", "side")
        self.succeeds(self.reword("HEAD^2~3"))
        self.assertEqual(self.message("HEAD^2~3"), b"reworded\n")
        self.assertEqual(self.message("HEAD^2~2"), b"side-2\n")
        self.assertEqual(self.oid("HEAD^1"), self.tip)
        self.assertEqual(self.oid("HEAD^2~4"), self.root)
        self.assertEqual(self.oid("side"), old_side[-1])

    def test_message_search_with_spaces_and_regex_anchor_performs_rewrite(self):
        self.commit("fix login")
        self.commit("after")
        self.succeeds(self.reword(":/^fix login"))
        self.assertEqual(self.message("HEAD~1"), b"reworded\n")
        self.assertEqual(self.message(), b"after\n")
        self.assertEqual(self.oid("HEAD~2"), self.tip)

    def test_annotated_tag_pointing_to_tree_is_rejected_before_editor(self):
        self.git("tag", "-a", "tree-tag", "-m", "tree tag", "HEAD^{tree}")
        result = self.reword("tree-tag")
        self.fails(result, "rev-parse")
        self.assertNotIn(b"Rewording ", result.stderr)
        self.assertEqual(self.oid(), self.tip)


if __name__ == "__main__":
    unittest.main()
