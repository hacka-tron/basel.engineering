"""The private About Basel repo as an about_me source, the sweep guard, the ignored public dir.

The checkout (``corpus/about-me-private/``, from hacka-tron/basel.engineering-docs)
exists only in images built with the deploy key. These tests build one in a
temporary directory; nothing private is read.
"""

import os
import re
from pathlib import Path

import pytest
import yaml

from services.glassbox.ingest import run as ingest_run
from services.glassbox.ingest.scanner import (
    scan_file,
    scan_sources,
    strip_front_matter,
)
from services.glassbox.ingest.sweep import ScopedDocument, plan_sweep, source_root
from services.glassbox.privacy import REDACTION, guard_document

REPO = Path(__file__).resolve().parents[2]
MODEL = "fake-v1"
FAKE_KEY_DOC = "# Keys\n\nkey = AKIA1234567890ABCDEF\n"  # pragma: allowlist secret


def _tree(root, files):
    for relative, text in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _about_me(root):
    return {s.source_path for s in scan_sources(root) if s.corpus == "about_me"}


def test_only_markdown_under_about_me_is_read_as_private_paths(tmp_path):
    _tree(
        tmp_path,
        {
            "corpus/about-me-private/README.md": "# Private docs repo\n",
            "corpus/about-me-private/LICENSE.md": "license\n",
            "corpus/about-me-private/other/notes.md": "# Not about-me\n",
            "corpus/about-me-private/about-me/notes.md": "# Notes\n",
            "corpus/about-me-private/about-me/roles/google.md": "# Google\n",
            "corpus/about-me-private/about-me/.drafts/wip.md": "# Draft\n",
            "corpus/about-me-private/about-me/photo.png": "not markdown",
            "corpus/about-me-private/.git/description.md": "# git\n",
        },
    )
    assert _about_me(tmp_path) == {"private/notes.md", "private/roles/google.md"}
    sources = {s.source_path: s for s in scan_sources(tmp_path)}
    assert scan_file(sources["private/roles/google.md"]).content == "# Google\n"


def test_symlinks_in_the_private_checkout_are_not_followed(tmp_path):
    outside = tmp_path / "outside.md"
    outside.write_text("# Not part of the corpus\n")
    private = tmp_path / "corpus" / "about-me-private" / "about-me"
    private.mkdir(parents=True)
    os.symlink(outside, private / "link.md")
    assert _about_me(tmp_path) == set()


def test_no_checkout_means_no_about_me_sources(tmp_path):
    _tree(tmp_path, {"corpus/about-me/bio.md": "# Bio\n"})
    assert _about_me(tmp_path) == set()


def test_private_files_pass_the_secret_scanner_and_the_personal_data_guard(tmp_path):
    _tree(
        tmp_path,
        {
            "corpus/about-me-private/about-me/contact.md": (
                "---\ntype: bio\n---\n# Contact\n\nCall 614-555-0100 or +44 20 7946 0958.\n"
            ),
            "corpus/about-me-private/about-me/keys.md": FAKE_KEY_DOC,
        },
    )
    sources = {s.source_path: s for s in scan_sources(tmp_path)}
    assert scan_file(sources["private/keys.md"]).error.startswith("possible AWS access key")
    scanned = scan_file(sources["private/contact.md"])
    guarded = guard_document(
        strip_front_matter(scanned.content), "private/contact.md", quarantine=frozenset()
    )
    assert guarded.counts == {"phone": 2}
    assert "555-0100" not in guarded.text and guarded.text.count(REDACTION) == 2


# --- A public corpus/about-me directory is ignored -------------------------------------


def test_a_public_about_me_directory_is_never_scanned(tmp_path):
    _tree(
        tmp_path,
        {
            "corpus/about-me/bio.md": "# Bio (public)\n",
            "corpus/about-me/skills.md": "# Skills (public)\n",
            "corpus/about-me-private/about-me/bio.md": "# Bio (private)\n",
        },
    )
    assert _about_me(tmp_path) == {"private/bio.md"}
    assert ingest_run.seen_source_paths(tmp_path)["about_me"] == {"private/bio.md"}
    assert not any(s.source_path.startswith("corpus/about-me/") for s in scan_sources(tmp_path))


# --- The stale sweep must never delete private documents it can't see -------------------


def _known():
    return [
        ScopedDocument(1, "corpus/about-me/bio.md", (1,)),
        ScopedDocument(2, "corpus/about-me/skills.md", (2,)),
        ScopedDocument(3, "private/notes.md", (3,)),
        ScopedDocument(4, "private/roles/google.md", (4,)),
    ]


def test_private_paths_have_their_own_source_root():
    assert source_root("private/roles/google.md") == "private"
    assert source_root("docs/DESIGN.md") == "docs"


@pytest.mark.parametrize("force", [False, True])
def test_a_release_without_the_private_checkout_never_sweeps_private_documents(force):
    """No deploy key (or the secret removed): the private files look deleted. Refuse."""
    public_only = {"corpus/about-me/bio.md", "corpus/about-me/skills.md"}
    plan = plan_sweep("about_me", MODEL, _known(), public_only, max_fraction=1.0, force=force)
    assert [d.source_path for d in plan.stale] == ["private/notes.md", "private/roles/google.md"]
    assert plan.refused and "private About Basel checkout" in plan.refused
    assert "--force-sweep does not override" in plan.refused


def test_a_file_deleted_from_the_private_repo_is_swept_normally():
    seen = {"corpus/about-me/bio.md", "corpus/about-me/skills.md", "private/notes.md"}
    plan = plan_sweep("about_me", MODEL, _known(), seen)
    assert [doc.source_path for doc in plan.stale] == ["private/roles/google.md"]
    assert plan.refused is None


def test_a_legacy_public_document_left_in_the_index_is_refused_by_the_directory_guard():
    """corpus/about-me is no longer scanned, so a leftover indexed public doc looks dropped."""
    seen = {"private/notes.md", "private/roles/google.md"}
    plan = plan_sweep("about_me", MODEL, _known(), seen)
    assert [doc.source_path for doc in plan.stale] == [
        "corpus/about-me/bio.md",
        "corpus/about-me/skills.md",
    ]
    assert plan.refused and "corpus" in plan.refused
    forced = plan_sweep("about_me", MODEL, _known(), seen, force=True)
    assert forced.refused is None


# --- Nothing private can reach Git, the image's extras, or public caches -------------


def test_the_checkout_is_ignored_by_git_and_only_about_me_markdown_enters_the_image():
    assert "corpus/about-me-private/" in (REPO / ".gitignore").read_text().splitlines()
    dockerignore = (REPO / ".dockerignore").read_text().splitlines()
    assert "corpus/about-me-private/**" in dockerignore
    assert "!corpus/about-me-private/about-me/**/*.md" in dockerignore
    # The exclusion comes first, so the exception is the only way in.
    assert dockerignore.index("corpus/about-me-private/**") < dockerignore.index(
        "!corpus/about-me-private/about-me/**/*.md"
    )
    # Prompt v18: the approved example answers (few-shots) are the one other file.
    exceptions = [line for line in dockerignore if line.startswith("!corpus/about-me-private/")]
    assert exceptions == [
        "!corpus/about-me-private/about-me/**/*.md",
        "!corpus/about-me-private/examples/approved-answers.yaml",
    ]
    assert dockerignore.index("corpus/about-me-private/**") < dockerignore.index(
        "!corpus/about-me-private/examples/approved-answers.yaml"
    )


def _release_steps():
    workflow = yaml.safe_load((REPO / ".github" / "workflows" / "release.yml").read_text())
    return workflow["jobs"]["build-and-push"]["steps"]


_CONDITIONS = {
    "steps.about_me.outputs.configured == 'true'": lambda configured: configured,
    "steps.about_me.outputs.configured != 'true'": lambda configured: not configured,
}


def _runs(step, configured: bool) -> bool:
    condition = step.get("if")
    if condition is None:
        return True
    # Only the two exact forms are allowed, so the evaluation below is the real one.
    assert condition in _CONDITIONS, condition
    return _CONDITIONS[condition](configured)


def test_release_checks_out_the_private_repo_safely():
    steps = _release_steps()
    checkout = [s for s in steps if s.get("name") == "Check out the private About Basel repo"]
    assert len(checkout) == 1
    step = checkout[0]
    assert step["if"] == "steps.about_me.outputs.configured == 'true'"
    assert step["with"] == {
        "repository": "hacka-tron/basel.engineering-docs",
        "ssh-key": "${{ secrets.ABOUT_ME_DEPLOY_KEY }}",
        "persist-credentials": False,
        "fetch-depth": 1,
        "path": "corpus/about-me-private",
    }
    assert "continue-on-error" not in step  # a configured key that fails fails the release
    # The "configured?" step sees only whether the secret is set, never its value.
    probe = next(s for s in steps if s.get("id") == "about_me")
    assert probe["env"] == {"ABOUT_ME_CONFIGURED": "${{ secrets.ABOUT_ME_DEPLOY_KEY != '' }}"}


@pytest.mark.parametrize("configured", [True, False])
def test_exactly_one_build_runs_and_the_private_one_never_touches_the_actions_cache(configured):
    """Evaluates the step conditions for both cases, so an expression slip can't hide."""
    steps = _release_steps()
    builds = [s for s in steps if str(s.get("uses", "")).startswith("docker/build-push-action@")]
    assert len(builds) == 2
    running = [s for s in builds if _runs(s, configured)]
    assert len(running) == 1
    build = running[0]
    env = build.get("env", {})
    assert env.get("DOCKER_BUILD_RECORD_UPLOAD") == "false"
    if configured:
        assert "cache-to" not in build["with"] and "cache-from" not in build["with"]
        assert env.get("DOCKER_BUILD_SUMMARY") == "false"
    else:
        assert build["with"]["cache-to"] == "type=gha,mode=max"
    # No computed cache settings anywhere (the `x && '' || y` pitfall).
    for step in builds:
        for key in ("cache-to", "cache-from"):
            assert "${{" not in str(step["with"].get(key, ""))
    # The checkout runs exactly when the private build does.
    checkout = next(s for s in steps if s.get("name") == "Check out the private About Basel repo")
    assert _runs(checkout, configured) is configured


def test_no_release_step_lists_or_prints_files():
    for step in _release_steps():
        run = step.get("run", "")
        assert not re.search(r"\b(ls|cat|find|tree|head|tail|xxd|base64)\b", run), run


def test_every_dockerfile_copy_source_is_tracked_in_git():
    """A build without the private checkout must not fail on an empty COPY source."""
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    ).stdout.splitlines()
    for line in (REPO / "Dockerfile").read_text().splitlines():
        parts = line.split()
        if not parts or parts[0] != "COPY" or any(p.startswith("--from") for p in parts):
            continue
        for source in [p for p in parts[1:-1] if not p.startswith("--")]:
            prefix = source.rstrip("/")
            assert any(
                f == prefix or f.startswith(prefix + "/") for f in tracked
            ), f"Dockerfile COPY source {source} has no tracked files"
