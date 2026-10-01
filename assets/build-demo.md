# Screenshot demo

Build the fictional park-control repository from the git-tools root:

```sh
python3 assets/create-jurassic-park.py
```

The generated repositories are ignored. The builder refuses to overwrite
existing demo paths. It only changes the generated repositories, and does not
change your global Git configuration.

To push the prepared history to the private `jeremy-dolan/jurassic-park` repo
and add three PRs, two issues, and demo commit statuses:

```sh
python3 assets/create-jurassic-park.py --publish
```

This requires authenticated Git and GitHub CLI access. The check statuses are
explicit screenshot fixtures (passing, failing, and pending), rather than
GitHub Actions runs. Reviews require another account; the demo uses ready and
draft PRs. Publication can be retried after interruption without duplicating
open PRs or issues. It never force-pushes.

The jeep-tour PR is "Vehicle movement attracts T. rex." The backup-power
issue is created last so it appears as the latest issue in the overview.

Capture the actual overview at 120 columns:

```sh
cd assets/jurassic-park
COLUMNS=120 ../../bin/git-overview --color
```

The checked-out `feat/raptor-doors` branch is two commits ahead and three behind
its upstream, with a merge conflict in `security/doors.py`. There are staged,
modified, and untracked files; five local branches; two additional worktrees;
one stash; and two release tags. `fix/tour-audio` has a deleted upstream.
The visitor-center worktree uses `feat/jeep-tour` and is clean. The lab uses
`feat/storm-tracker` and has unfinished wind-sensor calibration work. The storm
branch's latest commit is "Add tropical storm to opening-day forecast."
The main worktree's reflog uses synthetic operation times five minutes apart,
in recorded order. Commit dates remain backdated for the branch and tag ages.

Before publication, preview the full layout without network calls:

```sh
COLUMNS=120 python3 assets/create-jurassic-park.py --preview
```

This uses the actual local state with simulated GitHub data and the expected
post-publication upstream check. Numbers and ages may change when published.
Running git-overview normally before publication reports a missing upstream
because the GitHub repository is still empty. Publishing the prepared refs
resolves that warning; `fix/tour-audio` intentionally retains its gone status.

The temporary bare repository in `jurassic-park-worktrees/origin.git` seeds
genuine remote-tracking refs during setup; the finished demo uses GitHub as
its `origin`.
