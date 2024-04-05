#!/usr/bin/env python

# WARNING: DO NOT EDIT!
#
# This file was generated by plugin_template, and is managed by it. Please use
# './plugin-template --github pulp_python' to update this file.
#
# For more info visit https://github.com/pulp/plugin_template

import argparse
import re
import os
import yaml
from tempfile import TemporaryDirectory
from packaging.version import Version
from git import Repo

UPSTREAM_REMOTE = "https://github.com/pulp/pulp_python.git"
DEFAULT_BRANCH = "main"
RELEASE_BRANCH_REGEX = r"^([0-9]+)\.([0-9]+)$"
Y_CHANGELOG_EXTS = [".feature", ".removal", ".deprecation"]
Z_CHANGELOG_EXTS = [".bugfix", ".doc", ".misc"]


def main():
    """Check which branches need a release."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--branches",
        default="supported",
        help="A comma separated list of branches to check for releases. Can also use keyword: "
        "'supported'. Defaults to 'supported', see `supported_release_branches` in "
        "`plugin_template.yml`.",
    )
    opts = parser.parse_args()

    with TemporaryDirectory() as d:
        # Clone from upstream to ensure we have updated branches & main
        repo = Repo.clone_from(UPSTREAM_REMOTE, d, filter="blob:none")
        heads = [h.split("/")[-1] for h in repo.git.ls_remote("--heads").split("\n")]
        available_branches = [h for h in heads if re.search(RELEASE_BRANCH_REGEX, h)]
        available_branches.sort(key=lambda ver: Version(ver))
        available_branches.append(DEFAULT_BRANCH)

        branches = opts.branches
        if branches == "supported":
            with open(f"{d}/template_config.yml", mode="r") as f:
                tc = yaml.safe_load(f)
                branches = set(tc["supported_release_branches"])
            latest_release_branch = tc["latest_release_branch"]
            if latest_release_branch is not None:
                branches.add(latest_release_branch)
            branches.add(DEFAULT_BRANCH)
        else:
            branches = set(branches.split(","))

        if diff := branches - set(available_branches):
            print(f"Supplied branches contains non-existent branches! {diff}")
            exit(1)

        print(f"Checking for releases on branches: {branches}")

        releases = []
        for branch in branches:
            if branch != DEFAULT_BRANCH:
                # Check if a Z release is needed
                changes = repo.git.ls_tree("-r", "--name-only", f"origin/{branch}", "CHANGES/")
                z_changelog = False
                for change in changes.split("\n"):
                    # Check each changelog file to make sure everything checks out
                    _, ext = os.path.splitext(change)
                    if ext in Y_CHANGELOG_EXTS:
                        print(
                            f"Warning: A non-backported changelog ({change}) is present in the "
                            f"{branch} release branch!"
                        )
                    elif ext in Z_CHANGELOG_EXTS:
                        z_changelog = True

                last_tag = repo.git.describe("--tags", "--abbrev=0", f"origin/{branch}")
                req_txt_diff = repo.git.diff(
                    f"{last_tag}", f"origin/{branch}", "--name-only", "--", "requirements.txt"
                )
                if z_changelog or req_txt_diff:
                    # Blobless clone does not have file contents for Z branches,
                    # check commit message for last Z bump
                    git_branch = f"origin/{branch}"
                    next_version = None
                    bump_commit = repo.git.log(
                        "--oneline",
                        "--grep=Bump version",
                        "-n 1",
                        git_branch,
                        "--",
                        ".bumpversion.cfg",
                    )
                    if bump_commit:
                        next_version = bump_commit.split("→ ")[-1]
                    # If not found - try old-commit-msg
                    if not next_version:
                        bump_commit = repo.git.log(
                            "--oneline",
                            "--grep=Bump to",
                            "-n 1",
                            git_branch,
                            "--",
                            ".bumpversion.cfg",
                        )
                        next_version = bump_commit.split("to ")[-1] if bump_commit else None

                    # You could, theoretically, be next_vers==None here - but that's always
                    # been true for this script.
                    next_version = Version(next_version)
                    reason = "CHANGES" if z_changelog else "requirements.txt"
                    print(
                        f"A Z-release is needed for {branch}, "
                        f"Prev: {last_tag}, "
                        f"Next: {next_version.base_version}, "
                        f"Reason: {reason}"
                    )
                    releases.append(next_version)
            else:
                # Check if a Y release is needed
                changes = repo.git.ls_tree("-r", "--name-only", DEFAULT_BRANCH, "CHANGES/")
                for change in changes.split("\n"):
                    _, ext = os.path.splitext(change)
                    if ext in Y_CHANGELOG_EXTS:
                        # We don't put Y release bumps in the commit message, check file instead
                        # The 'current_version' is always the next version to release
                        next_version = repo.git.grep(
                            "current_version", DEFAULT_BRANCH, "--", ".bumpversion.cfg"
                        ).split("=")[-1]
                        next_version = Version(next_version)
                        print(
                            f"A new Y-release is needed! New Version: {next_version.base_version}"
                        )
                        releases.append(next_version)
                        break

        if len(releases) == 0:
            print("No new releases to perform.")


if __name__ == "__main__":
    main()
