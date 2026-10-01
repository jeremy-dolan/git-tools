#!/usr/bin/env python3
"""Create the screenshot repository; optionally publish its GitHub fixtures."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time


ASSETS = Path(__file__).resolve().parent
REPO = ASSETS / "jurassic-park"
TREES = ASSETS / "jurassic-park-worktrees"
MANIFEST = TREES / "demo.json"
GITHUB = "jeremy-dolan/jurassic-park"
URL = "https://github.com/" + GITHUB + ".git"
NOW = int(time.time())


def run(*args, cwd=None, env=None, check=True):
    return subprocess.run(args, cwd=REPO if cwd is None else cwd, env=env, check=check,
                          text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def git(*args, **kwargs):
    return run("git", *args, **kwargs).stdout.strip()


def dated(hours):
    stamp = datetime.fromtimestamp(NOW - int(hours * 3600), timezone.utc).isoformat()
    return dict(os.environ, GIT_AUTHOR_DATE=stamp, GIT_COMMITTER_DATE=stamp)


def write(path, content, root=None):
    target = (REPO if root is None else root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)


def commit(message, hours):
    git("add", ".")
    git("commit", "-m", message, env=dated(hours))
    return git("rev-parse", "HEAD")


def config(backup):
    write("security/fences.py", 'POWER_ENABLED = True\nBACKUP_SOURCE = "' + backup + '"\n')


def normalize_demo_reflog():
    """Give demo operations a steady timeline without changing commit dates."""
    path = Path(git("rev-parse", "--path-format=absolute", "--git-path", "logs/HEAD"))
    rows = path.read_bytes().splitlines(keepends=True)
    normalized = []
    for index, row in enumerate(rows):
        header, message = row.split(b"\t", 1)
        identity, _, zone = header.rsplit(b" ", 2)
        timestamp = str(NOW - (len(rows) - index) * 300).encode("ascii")
        normalized.append(identity + b" " + timestamp + b" " + zone + b"\t" + message)
    path.write_bytes(b"".join(normalized))


def build(storm_source=None):
    if REPO.exists() or TREES.exists():
        raise SystemExit("Demo paths already exist; refusing to overwrite them.")
    REPO.mkdir()
    TREES.mkdir()
    seed = TREES / "origin.git"
    git("init", "--bare", "--initial-branch=main", str(seed), cwd=ASSETS)
    git("init", "--initial-branch=main")
    git("config", "user.name", "Jurassic Park Engineering")
    git("config", "user.email", "engineering@example.com")
    git("config", "commit.gpgsign", "false")
    git("config", "tag.gpgsign", "false")
    git("config", "core.hooksPath", "/dev/null")
    write("README.md", "# Jurassic Park\n\nFictional park control software for the git-overview screenshot.\n\nSpared no expense. Except on redundancy.\n")
    write(".gitignore", "__pycache__/\n")
    config("generator")
    write("security/doors.py", "RAPTOR_DOOR_DETECTION = False\n")
    write("tours/route.txt", "visitor-center -> herbivore-paddock -> visitor-center\n")
    write("ops/power.md", "Backup power must operate independently of the grid.\n")
    write("ops/staffing.md", "Two engineers on every shift.\n")
    write("tests/test_safety.py", 'import runpy\nimport unittest\nfrom pathlib import Path\n\n\nclass SafetyTests(unittest.TestCase):\n    def test_fences_have_independent_power(self):\n        settings = runpy.run_path(str(Path(__file__).resolve().parents[1] / "security/fences.py"))\n        self.assertTrue(settings["POWER_ENABLED"])\n        self.assertEqual(settings["BACKUP_SOURCE"], "generator")\n')
    commit("Install park control systems", 21 * 24)
    write("ops/opening-day.md", "Opening checklist: fences, tours, lunch.\n")
    commit("Prepare opening-day checklist", 14 * 24)
    git("tag", "-a", "v0.9.0", "-m", "Preview: dinosaurs still behind fences", env=dated(14 * 24))

    # Keep one real stash so the overview fits in fewer rows.
    write("ops/staffing.md", "One engineer should be plenty.\n")
    git("stash", "push", "-m", "budget cuts, probably fine", env=dated(26))

    write("ops/opening-day.md", "Opening checklist: fences, tours, lunch, independent backup power.\n")
    main = commit("Verify backup power before opening day", 48)
    git("tag", "-a", "v1.0.0-opening-day", "-m", "The park is ready. Probably.", env=dated(48))
    git("remote", "add", "origin", str(seed))
    git("push", "-u", "origin", "main", "--tags")

    git("switch", "-c", "fix/tour-audio", "main~2")
    write("tours/audio.md", "Restore narration in tour vehicles before opening day.\n")
    commit("Restore narration in tour vehicles", 30)
    git("push", "-u", "origin", "fix/tour-audio")
    git("push", "origin", "--delete", "fix/tour-audio")

    git("switch", "-c", "feat/jeep-tour", "main")
    write("tours/route.txt", "visitor-center -> herbivore-paddock -> scenic-overlook -> visitor-center\n")
    tour = commit("Keep the tour away from the T. rex", 4)
    git("push", "-u", "origin", "feat/jeep-tour")
    write("tours/guide.txt", "Please keep your hands inside the vehicle.\n")
    commit("Remind guests to stay in the vehicle", 2)

    git("switch", "-c", "feat/raptor-doors", "main")
    write("security/doors.py", "RAPTOR_DOOR_DETECTION = True\n")
    doors = commit("Detect raptors operating door handles", 3)
    git("push", "-u", "origin", "feat/raptor-doors")
    write("security/doors.py", "RAPTOR_DOOR_DETECTION = True\nDOOR_HANDLE_HEIGHT_CM = 90\n")
    commit("Add sensors beside kitchen door handles", 1)
    write("security/alerts.md", "Notify park staff when a raptor opens a door.\n")
    commit("Alert when a raptor opens a door", 0.5)
    local_doors = git("rev-parse", "HEAD")

    # Advance the remote independently, then fetch: authentic 2 ahead / 3 behind.
    git("switch", "--detach", doors)
    write("security/doors.py", "RAPTOR_DOOR_DETECTION = True\nDOOR_HANDLE_HEIGHT_CM = 150\n")
    commit("Raise door sensors above handle height", 2.5)
    write("ops/staffing.md", "One engineer on every shift. What could go wrong?\n")
    commit("Reduce the overnight engineering shift", 2)
    write("ops/alarms.md", "Display door alarms in the park control room.\n")
    doors = commit("Display door alarms in the control room", 1.75)
    git("push", "origin", "HEAD:refs/heads/feat/raptor-doors")

    git("switch", "-c", "feat/storm-tracker", "main")
    if storm_source:
        source, storm = storm_source
        git("fetch", str(source), storm)
        git("read-tree", "--reset", "-u", storm)
        git("update-ref", "-m", "Reuse opening-day storm forecast commit",
            "refs/heads/feat/storm-tracker", storm)
    else:
        write("ops/weather.md", "Tropical storm approaching Isla Nublar.\n")
        storm = commit("Add tropical storm to opening-day forecast", 1.5)
    git("push", "-u", "origin", "feat/storm-tracker")
    git("fetch", "origin")
    git("switch", "feat/raptor-doors")

    visitor = TREES / "visitor-center"
    lab = TREES / "lab"
    git("worktree", "add", str(visitor), "feat/jeep-tour")
    git("worktree", "add", str(lab), "feat/storm-tracker")
    write("ops/weather.md", "Tropical storm approaching Isla Nublar.\nCalibrate wind sensors before opening day.\n", root=lab)
    write("lab/observations.md", "Wind readings disagree with the mainland forecast.\n", root=lab)

    merge = run("git", "merge", "--no-edit", "origin/feat/raptor-doors", check=False)
    if merge.returncode != 1 or "UU security/doors.py" not in git("status", "--porcelain"):
        raise SystemExit("Expected door-sensor merge conflict was not created: " + merge.stderr)
    write("ops/evacuation.md", "Evacuate guests before testing raptor door sensors.\n")
    git("add", "ops/evacuation.md")
    write("tours/route.txt", "visitor-center -> emergency-dock\n")
    write("ops/power.md", "Keep the backup generator running while calibrating door sensors.\n")
    write("evacuation-plan.md", "Find the nearest boat. Bring the children.\n")

    git("remote", "set-url", "origin", URL)
    data = {"main": main, "storm": storm, "tour": tour, "doors": doors,
            "local_doors": local_doors, "published": False}
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n")
    normalize_demo_reflog()
    print("Created " + str(REPO))
    print("Remote: " + URL)
    print("Preview: python3 assets/create-jurassic-park.py --preview")
    print("Publish: python3 assets/create-jurassic-park.py --publish")


def api(endpoint, *fields):
    args = ["gh", "api", endpoint]
    for key, value in fields:
        args.extend(["-f", key + "=" + value])
    return json.loads(run(*args).stdout)


def preview():
    """Render the local state with explicitly simulated post-publication data."""
    os.chdir(REPO)
    module = runpy.run_path(str(Path(__file__).resolve().parents[1] / "bin/git-overview"),
                            run_name="preview")
    namespace = module["main"].__globals__
    original_head = namespace["collect_head"]

    def preview_head(git, options):
        head = original_head(git, options)
        head.remote_state = "same"
        return head

    namespace["collect_head"] = preview_head
    namespace["collect_github"] = lambda options, has_remote: module["GitHubOverview"](
        "", prs=[
            dict(number=3, title="Track opening-day storms", headRefName="feat/storm-tracker",
                 isDraft=False, reviewDecision=None, ci="FAILURE"),
            dict(number=2, title="Vehicle movement attracts T. rex", headRefName="feat/jeep-tour",
                 isDraft=False, reviewDecision=None, ci="SUCCESS"),
            dict(number=1, title="Detect raptors opening doors", headRefName="feat/raptor-doors",
                 isDraft=True, reviewDecision=None, ci="PENDING"),
        ], issue_count=2,
        newest_issue=dict(number=5, title="Backup power depends on one employee", timestamp=int(time.time())))
    sys.argv = ["git-overview", "--no-check-remote", "--no-color"]
    print("Preview: GitHub data and remote freshness are simulated; numbers and ages may change.")
    raise SystemExit(module["main"]())


def publish():
    data = json.loads(MANIFEST.read_text())
    if data["published"]:
        raise SystemExit("Already published; refusing to create duplicate fixtures.")
    # A preexisting repo may have appeared since the local build. Never force-push.
    remote = git("ls-remote", "--heads", "origin")
    intended = {"main": data["main"], "feat/storm-tracker": data["storm"],
                "feat/jeep-tour": data["tour"], "feat/raptor-doors": data["doors"]}
    for row in remote.splitlines():
        sha, ref = row.split()
        name = ref.removeprefix("refs/heads/")
        if name not in intended or sha != intended[name]:
            raise SystemExit("Remote has unexpected history; refusing to overwrite it.")
    git("push", "--atomic", "origin", *(sha + ":refs/heads/" + name for name, sha in intended.items()),
        "refs/tags/v0.9.0", "refs/tags/v1.0.0-opening-day")

    # Seed explicit demo commit statuses, keeping screenshot results stable.
    for key, state, description in (
        ("storm", "failure", "Weather station wind sensors need calibration"),
        ("tour", "success", "Tour route avoids all carnivore paddocks"),
        ("doors", "pending", "Awaiting door-handle tests in the raptor lab"),
    ):
        api("repos/" + GITHUB + "/statuses/" + data[key],
            ("state", state), ("context", "demo/park-safety"), ("description", description))

    existing = api("repos/" + GITHUB + "/pulls?state=open&per_page=100")
    for branch, title, draft, body in (
        ("feat/raptor-doors", "Detect raptors opening doors", True, "Spared no expense."),
        ("feat/jeep-tour", "Vehicle movement attracts T. rex", False,
         "Tour routing needs another safety review."),
        ("feat/storm-tracker", "Track opening-day storms", False, "Spared no expense."),
    ):
        match = next((pr for pr in existing if pr["head"]["ref"] == branch), None)
        if match:
            continue
        args = ["gh", "pr", "create", "--repo", GITHUB, "--base", "main", "--head", branch,
                "--title", title, "--body", "Fictional git-overview screenshot fixture.\n\n" + body]
        if draft:
            args.append("--draft")
        print(run(*args).stdout.strip())
    existing_issues = api("repos/" + GITHUB + "/issues?state=open&per_page=100")
    for title, body in (
        ("Raptors can open doors", "Door handles were not in the threat model."),
        ("Backup power depends on one employee", "Add redundancy before opening day."),
    ):
        if any(issue["title"] == title and "pull_request" not in issue for issue in existing_issues):
            continue
        print(run("gh", "issue", "create", "--repo", GITHUB, "--title", title,
                  "--body", "Fictional git-overview screenshot fixture.\n\n" + body).stdout.strip())
    data["published"] = True
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n")
    print("Published PRs, issues, and demo check statuses to " + GITHUB)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--publish", action="store_true", help="Push demo history and create GitHub fixtures")
    mode.add_argument("--preview", action="store_true", help="Preview with simulated GitHub data and no network calls")
    args = parser.parse_args()
    try:
        if args.publish:
            publish()
        elif args.preview:
            preview()
        else:
            build()
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr or str(error))
