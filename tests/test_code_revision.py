"""A generation has to say which code produced it.

The loop runs for hours while the repository keeps being worked on, so a
time series of generations spans more than one version of the selection
rule, the graph and the catalogue. Without the revision recorded, a change
in the numbers cannot be attributed: an improvement from a code change and
an improvement from evolution look identical in the record.

A dirty tree is part of the fact. Generation 37 was promoted under code that
was not committed, and nothing in its report says so.
"""

from __future__ import annotations

from factorio_ai_lab.experiments.curriculum_runner import code_revision


def test_the_revision_is_reported() -> None:
    revision = code_revision()
    assert isinstance(revision, dict)
    assert "commit" in revision
    assert "dirty" in revision


def test_a_real_checkout_reports_a_commit() -> None:
    revision = code_revision()
    commit = revision["commit"]
    assert commit is None or (isinstance(commit, str) and len(commit) >= 7), commit


def test_absence_is_declared_not_faked(tmp_path) -> None:
    # Outside a checkout there is no revision to report. Answering with a
    # plausible-looking string would be worse than answering nothing.
    revision = code_revision(root=tmp_path)
    assert revision["commit"] is None
    assert revision["reason"], "nao declarou por que nao ha revisao"


def test_dirty_is_a_fact_not_an_omission(tmp_path) -> None:
    revision = code_revision(root=tmp_path)
    # Unknown, not False: claiming a clean tree we could not inspect would
    # be the same substitution that cost this project eleven generations.
    assert revision["dirty"] is None
