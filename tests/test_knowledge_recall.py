"""Verified knowledge has to reach the decision, and stay auditable after it.

The log had accumulated 370 lessons and its only use was counting lines: no
lesson was ever read to decide anything. These tests pin the three properties
that make a recall worth having.

Only a lesson whose verification passed may enter, and the size of both
populations travels with the selection, so nobody reads "the advice used
knowledge" without knowing how much was discarded. The selection is justified
by the stage of the current bottleneck with recency as the tie-break, and the
tests show the criterion discriminates: change the stage and the selection
changes. What entered the decision is written into the generation report by
identifier, so a later reader can ask whether advice that cited a lesson
produced a different result.

Absence is never silence. A missing log, an empty one, or a line half-written
by the appending writer is counted and declared, never answered with an empty
list that reads as "there is no relevant lesson".
"""

from __future__ import annotations

import ast
import json
import pathlib
from collections.abc import Iterator
from contextlib import contextmanager
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import pytest

from factorio_ai_lab.agents.evolution_advisor import _advisor_payload
from factorio_ai_lab.experiments import curriculum_runner
from factorio_ai_lab.learning.knowledge import (
    RECALL_BASIS_EMPTY_LOG,
    RECALL_BASIS_MISSING_LOG,
    RECALL_BASIS_NO_VERIFIED_LESSON,
    RECALL_BASIS_NOT_CONSULTED,
    RECALL_BASIS_RECENT_VERIFIED,
    RECALL_BASIS_STAGE_MATCH,
    RECALL_BASIS_UNREADABLE_LOG,
    recall_not_consulted,
    recall_verified_lessons,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

#: The log this lab actually produced, used as the known positive case: an
#: instrument that cannot find verified lessons in it is not measuring.
REAL_LOG = REPO_ROOT / "runs" / "knowledge.jsonl"

RUNNER_SOURCE = (
    REPO_ROOT / "src" / "factorio_ai_lab" / "experiments" / "curriculum_runner.py"
).read_text(encoding="utf-8")


def lesson_record(
    *,
    at: str,
    stage: str,
    lesson: str,
    verified: bool | None = True,
    hypothesis: str = "Repeat the measurement next generation.",
) -> dict[str, Any]:
    """One knowledge record shaped exactly like the ones on disk."""
    record: dict[str, Any] = {
        "at": at,
        "stage": stage,
        "lesson": lesson,
        "next_hypothesis": hypothesis,
        "facts": {"accepted": True, "output": 18.0},
        "evidence_keys": ["output"],
        "provider": {"model": "qwen3-4b", "provider_id": "local-qwen"},
        "source": "llm_verified" if verified else "deterministic_fallback",
    }
    if verified is not None:
        record["verification"] = {
            "verified": verified,
            "unsupported_numbers": [],
            "unknown_evidence_keys": [],
            "rate_claim_without_rate_fact": False,
        }
    return record


def write_log(path: pathlib.Path, records: list[dict[str, Any]]) -> pathlib.Path:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in records),
        encoding="utf-8",
    )
    return path


@contextmanager
def sandboxed_runs(root: pathlib.Path) -> Iterator[None]:
    """Point every file the runner writes at a temporary directory."""
    with patch.multiple(
        curriculum_runner,
        RESEARCH_STATE=root / "research_state.json",
        ACTIVE_RUN=root / "active_run.json",
        RESEARCH_HISTORY=root / "research",
        EVOLUTION_CHAMPION=root / "evolution_champion.json",
        EVOLUTION_HISTORY=root / "evolution_history.jsonl",
        GENERATION_REPORTS=root / "generation_reports",
        KNOWLEDGE_LOG=root / "knowledge.jsonl",
        NICHE_ARCHIVE=root / "niche_archive.json",
    ):
        yield


def test_a_lesson_that_failed_verification_never_enters_the_decision(
    tmp_path: pathlib.Path,
) -> None:
    log = write_log(
        tmp_path / "knowledge.jsonl",
        [
            lesson_record(
                at="2026-09-22T10:00:00+00:00",
                stage="copper_smelting",
                lesson="Copper smelting reached 99 plates.",
                verified=False,
            ),
            lesson_record(
                at="2026-09-22T11:00:00+00:00",
                stage="copper_smelting",
                lesson="Copper smelting output was 18.0 plates.",
            ),
        ],
    )
    recall = recall_verified_lessons(log, stage="copper_smelting")
    assert [row.lesson for row in recall.lessons] == [
        "Copper smelting output was 18.0 plates."
    ]
    assert recall.verified_count == 1
    assert recall.unverified_count == 1, "the discarded population was not counted"


def test_a_record_without_a_verification_block_counts_as_discarded(
    tmp_path: pathlib.Path,
) -> None:
    # 81 of the 370 records on disk predate the verification field. Absent is
    # not verified; the gate is `verified is True`, not truthiness.
    log = write_log(
        tmp_path / "knowledge.jsonl",
        [
            lesson_record(
                at="2026-09-21T19:07:48+00:00",
                stage="baseline_mining",
                lesson="An old lesson with no verification block.",
                verified=None,
            )
        ],
    )
    recall = recall_verified_lessons(log, stage="baseline_mining")
    assert recall.lessons == ()
    assert recall.verified_count == 0
    assert recall.unverified_count == 1
    assert recall.basis == RECALL_BASIS_NO_VERIFIED_LESSON


def test_the_stage_decides_which_lessons_are_selected(
    tmp_path: pathlib.Path,
) -> None:
    log = write_log(
        tmp_path / "knowledge.jsonl",
        [
            lesson_record(
                at="2026-09-22T10:00:00+00:00",
                stage="copper_smelting",
                lesson="Copper plates accumulated in the chest.",
            ),
            lesson_record(
                at="2026-09-22T11:00:00+00:00",
                stage="baseline_mining",
                lesson="The drill fed the chest without stalling.",
            ),
            lesson_record(
                at="2026-09-22T12:00:00+00:00",
                stage="copper_smelting",
                lesson="The furnace kept burning through the window.",
            ),
        ],
    )
    copper = recall_verified_lessons(log, stage="copper_smelting", limit=2)
    baseline = recall_verified_lessons(log, stage="baseline_mining", limit=2)

    assert {row.stage for row in copper.lessons} == {"copper_smelting"}
    assert {row.stage for row in baseline.lessons} == {"baseline_mining"}
    assert copper.lesson_ids != baseline.lesson_ids, (
        "the stage does not discriminate: both bottlenecks recall the same set"
    )
    assert copper.basis == RECALL_BASIS_STAGE_MATCH
    assert copper.stage_verified_count == 2
    assert baseline.stage_verified_count == 1


def test_recency_breaks_the_tie_inside_one_stage(tmp_path: pathlib.Path) -> None:
    # File order is deliberately not chronological order, so a selection that
    # took the last lines instead of the newest timestamps fails here.
    log = write_log(
        tmp_path / "knowledge.jsonl",
        [
            lesson_record(
                at="2026-09-22T12:00:00+00:00",
                stage="copper_smelting",
                lesson="newest",
            ),
            lesson_record(
                at="2026-09-22T09:00:00+00:00",
                stage="copper_smelting",
                lesson="oldest",
            ),
            lesson_record(
                at="2026-09-22T10:00:00+00:00",
                stage="copper_smelting",
                lesson="middle",
            ),
        ],
    )
    recall = recall_verified_lessons(log, stage="copper_smelting", limit=2)
    assert [row.lesson for row in recall.lessons] == ["newest", "middle"]


def test_a_bottleneck_with_no_lesson_falls_back_and_says_so(
    tmp_path: pathlib.Path,
) -> None:
    # Generation 37 was bottlenecked on `Logistic science`, a stage that
    # writes no lesson. An empty answer would read as "nothing was learned".
    log = write_log(
        tmp_path / "knowledge.jsonl",
        [
            lesson_record(
                at="2026-09-22T10:00:00+00:00",
                stage="baseline_mining",
                lesson="The drill fed the chest without stalling.",
            )
        ],
    )
    recall = recall_verified_lessons(log, stage="logistic_science")
    assert recall.basis == RECALL_BASIS_RECENT_VERIFIED
    assert recall.stage_verified_count == 0
    assert [row.stage for row in recall.lessons] == ["baseline_mining"]


def test_a_missing_log_is_declared_instead_of_read_as_no_lesson(
    tmp_path: pathlib.Path,
) -> None:
    recall = recall_verified_lessons(tmp_path / "absent.jsonl", stage="baseline_mining")
    assert recall.basis == RECALL_BASIS_MISSING_LOG
    assert recall.log_present is False
    assert recall.lessons == ()
    assert recall.to_dict()["basis"] == RECALL_BASIS_MISSING_LOG


def test_an_empty_log_is_declared(tmp_path: pathlib.Path) -> None:
    log = tmp_path / "knowledge.jsonl"
    log.write_text("", encoding="utf-8")
    recall = recall_verified_lessons(log, stage="baseline_mining")
    assert recall.basis == RECALL_BASIS_EMPTY_LOG
    assert recall.log_present is True
    assert recall.lessons == ()


def test_a_log_that_cannot_be_read_is_declared(tmp_path: pathlib.Path) -> None:
    # A path that is not a readable file answers a basis of its own instead
    # of the same empty selection a verified-but-silent log would answer.
    unreadable = tmp_path / "knowledge.jsonl"
    unreadable.mkdir()
    recall = recall_verified_lessons(unreadable, stage="baseline_mining")
    assert recall.basis == RECALL_BASIS_UNREADABLE_LOG
    assert recall.lessons == ()
    assert recall.log_present is False


def test_a_malformed_line_is_counted_and_the_read_continues(
    tmp_path: pathlib.Path,
) -> None:
    # The writer appends while the reader reads, so the last line can be half
    # written. Skipping it must not cost the lessons that follow it.
    log = tmp_path / "knowledge.jsonl"
    rows = [
        json.dumps(
            lesson_record(
                at="2026-09-22T10:00:00+00:00",
                stage="copper_smelting",
                lesson="first",
            ),
            sort_keys=True,
        ),
        '{"at": "2026-09-22T11:00:00+00:00", "stage": "copper_sme',
        json.dumps(
            lesson_record(
                at="2026-09-22T12:00:00+00:00",
                stage="copper_smelting",
                lesson="second",
            ),
            sort_keys=True,
        ),
    ]
    log.write_text("\n".join(rows) + "\n", encoding="utf-8")
    recall = recall_verified_lessons(log, stage="copper_smelting", limit=5)
    assert [row.lesson for row in recall.lessons] == ["second", "first"]
    assert recall.malformed_lines == 1, "the unreadable line was not counted"
    assert recall.verified_count == 2


def test_provenance_names_every_lesson_that_entered(
    tmp_path: pathlib.Path,
) -> None:
    log = write_log(
        tmp_path / "knowledge.jsonl",
        [
            lesson_record(
                at="2026-09-22T10:00:00+00:00",
                stage="copper_smelting",
                lesson="first",
            ),
            lesson_record(
                at="2026-09-22T12:00:00+00:00",
                stage="copper_smelting",
                lesson="second",
            ),
        ],
    )
    recall = recall_verified_lessons(log, stage="copper_smelting", limit=2)
    payload = recall.to_dict()
    assert recall.lesson_ids == (
        "2026-09-22T12:00:00+00:00",
        "2026-09-22T10:00:00+00:00",
    )
    assert payload["lesson_ids"] == list(recall.lesson_ids)
    assert payload["verified_count"] == 2
    assert payload["unverified_count"] == 0
    assert payload["stage"] == "copper_smelting"
    assert [row["at"] for row in payload["lessons"]] == list(recall.lesson_ids)


def test_the_real_knowledge_log_yields_its_verified_lessons() -> None:
    if not REAL_LOG.exists():
        pytest.skip(f"{REAL_LOG} is absent in this environment")
    expected_lines = 0
    expected_verified = 0
    for line in REAL_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected_lines += 1
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        verification = row.get("verification")
        if isinstance(verification, dict) and verification.get("verified") is True:
            expected_verified += 1

    recall = recall_verified_lessons(REAL_LOG, stage="baseline_mining")
    assert recall.verified_count == expected_verified
    assert (
        recall.verified_count + recall.unverified_count + recall.malformed_lines
        == expected_lines
    )
    # Measured 2026-09-23: 163 verified of 370 records. The log is appended to,
    # never rewritten, so this floor only rises.
    assert recall.verified_count >= 163
    assert recall.basis == RECALL_BASIS_STAGE_MATCH
    assert len(recall.lessons) == 3
    assert {row.stage for row in recall.lessons} == {"baseline_mining"}


def test_every_mapped_knowledge_stage_occurs_in_the_real_log() -> None:
    # The stage map is a claim about what the runner writes; this is what
    # keeps it from becoming an invention.
    if not REAL_LOG.exists():
        pytest.skip(f"{REAL_LOG} is absent in this environment")
    written = set()
    for line in REAL_LOG.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            written.add(json.loads(line).get("stage"))
        except json.JSONDecodeError:
            continue
    mapped = set(curriculum_runner.KNOWLEDGE_STAGE_BY_CURRICULUM_NAME.values())
    assert mapped <= written, f"mapped stages never written: {sorted(mapped - written)}"


def test_the_stage_map_only_names_curriculum_stages() -> None:
    with TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        with sandboxed_runs(root):
            journal = curriculum_runner.ResearchJournal("curriculum-test")
    names = {str(stage["name"]) for stage in journal.state["curriculum"]}
    mapped = set(curriculum_runner.KNOWLEDGE_STAGE_BY_CURRICULUM_NAME)
    assert mapped <= names, f"unknown curriculum stages: {sorted(mapped - names)}"


def test_the_bottleneck_of_the_previous_run_picks_the_knowledge_stage() -> None:
    previous = {
        "stage": "Capability survival soak",
        "curriculum": [
            {"name": "Baseline iron mining", "status": "completed"},
            {"name": "Copper smelting", "status": "failed"},
            {"name": "Automation science", "status": "failed"},
        ],
    }
    assert curriculum_runner.bottleneck_stage_name(previous) == "Copper smelting"
    assert curriculum_runner.knowledge_stage_for("Copper smelting") == "copper_smelting"
    # With nothing failed, the stage the run stopped on is the bottleneck.
    assert (
        curriculum_runner.bottleneck_stage_name(
            {"stage": "Automation science", "curriculum": []}
        )
        == "Automation science"
    )
    assert curriculum_runner.bottleneck_stage_name({}) is None
    assert curriculum_runner.knowledge_stage_for("Logistic science") is None


def test_the_recalled_lessons_reach_the_advisor_payload(
    tmp_path: pathlib.Path,
) -> None:
    log = write_log(
        tmp_path / "knowledge.jsonl",
        [
            lesson_record(
                at="2026-09-22T10:00:00+00:00",
                stage="copper_smelting",
                lesson="A discarded claim about 99 plates.",
                verified=False,
            ),
            lesson_record(
                at="2026-09-23T04:01:41+00:00",
                stage="copper_smelting",
                lesson="Copper smelting output was 18.0 plates.",
            ),
        ],
    )
    recall = recall_verified_lessons(log, stage="copper_smelting")
    context = curriculum_runner.build_advisor_context(
        champion_configuration={"routing_turn_penalty": 0.505, "buffer_target": 12},
        previous_research={
            "run_id": "curriculum-20260923T035414Z",
            "arena": "lab_play",
            "status": "partial_success",
            "stage": "Copper smelting",
            "detail": "the furnace ran out of fuel",
            "metrics": {"copper_plate_output": 18.0},
        },
        candidate={"routing_turn_penalty": 0.52, "buffer_target": 12},
        recall=recall,
    )
    assert context["verified_lessons"], "the context carries no lesson"

    payload = _advisor_payload(context)
    decoded = json.loads(payload)
    assert "verified_lessons" in decoded, "the recall did not survive the budgeter"
    assert "copper_smelting: Copper smelting output was 18.0 plates." in payload, (
        "the lesson text did not reach the advisor payload"
    )
    assert "discarded claim" not in payload, "a rejected lesson reached the advisor"
    assert decoded["knowledge_recall"]["discarded"] == 1
    assert decoded["knowledge_recall"]["lesson_ids"] == ["2026-09-23T04:01:41+00:00"]


def test_a_squeezed_payload_loses_the_oldest_lesson_first(
    tmp_path: pathlib.Path,
) -> None:
    # The advisor's budgeter keeps the tail of a list when it has to drop
    # rows. Emitting the lessons oldest first is what decides which lesson
    # survives a context under pressure.
    log = write_log(
        tmp_path / "knowledge.jsonl",
        [
            lesson_record(
                at="2026-09-22T09:00:00+00:00",
                stage="copper_smelting",
                lesson="the oldest measurement",
            ),
            lesson_record(
                at="2026-09-22T10:00:00+00:00",
                stage="copper_smelting",
                lesson="the middle measurement",
            ),
            lesson_record(
                at="2026-09-23T04:00:00+00:00",
                stage="copper_smelting",
                lesson="the newest measurement",
            ),
        ],
    )
    recall = recall_verified_lessons(log, stage="copper_smelting", limit=3)
    squeezed = {
        **recall.to_advisor_context(),
        **{f"filler_{index}": "x" * 400 for index in range(28)},
    }
    payload = _advisor_payload(squeezed)
    assert "the newest measurement" in payload, "the budget dropped the newest lesson"
    assert "the oldest measurement" not in payload


def test_a_real_sized_context_still_carries_the_lesson_text() -> None:
    # The budgeter shortens by depth: nested one level deeper, each lesson was
    # cut to two fields and the text was the field dropped. This measures the
    # surviving shape against a context the size the loop really builds.
    if not REAL_LOG.exists():
        pytest.skip(f"{REAL_LOG} is absent in this environment")
    recall = recall_verified_lessons(
        REAL_LOG,
        stage="copper_smelting",
        limit=curriculum_runner.KNOWLEDGE_RECALL_LIMIT,
    )
    context = curriculum_runner.build_advisor_context(
        champion_configuration={f"param_{index}": index for index in range(20)},
        previous_research={
            "run_id": "curriculum-20260923T035414Z",
            "arena": "lab_play",
            "status": "partial_success",
            "stage": "Copper smelting",
            "detail": "the furnace ran out of fuel mid-window",
            "next_action": "refuel the copper line before scaling",
            "metrics": {f"metric_{index}": float(index) for index in range(40)},
        },
        candidate={f"gene_{index}": index / 10 for index in range(20)},
        recall=recall,
    )
    payload = _advisor_payload(context)
    assert len(payload) <= 2600, f"the payload overran its budget: {len(payload)}"
    head = recall.lessons[0].lesson[:40]
    assert head in payload, f"the lesson text was budgeted away: {payload}"


def test_the_generation_report_records_which_lessons_were_used(
    tmp_path: pathlib.Path,
) -> None:
    log = write_log(
        tmp_path / "knowledge.jsonl",
        [
            lesson_record(
                at="2026-09-23T04:01:41+00:00",
                stage="copper_smelting",
                lesson="Copper smelting output was 18.0 plates.",
            )
        ],
    )
    recall = recall_verified_lessons(log, stage="copper_smelting")
    with TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        with sandboxed_runs(root):
            journal = curriculum_runner.ResearchJournal("curriculum-test")
            journal.state["evolution"]["knowledge_recall"] = recall.to_dict()
            curriculum_runner.finalize_evolution_selection(
                journal,
                achieved={"iron_backbone"},
                physical_graph=None,
            )
            report = json.loads(
                [
                    line
                    for line in (root / "evolution_history.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ][-1]
            )
    assert report["knowledge_recall"]["lesson_ids"] == ["2026-09-23T04:01:41+00:00"]
    assert report["knowledge_recall"]["basis"] == RECALL_BASIS_STAGE_MATCH
    assert report["knowledge_recall"]["verified_count"] == 1


def test_a_generation_that_did_not_consult_knowledge_says_so() -> None:
    with TemporaryDirectory() as raw:
        root = pathlib.Path(raw)
        with sandboxed_runs(root):
            journal = curriculum_runner.ResearchJournal("curriculum-test")
            curriculum_runner.finalize_evolution_selection(
                journal,
                achieved=set(),
                physical_graph=None,
            )
            report = json.loads(
                [
                    line
                    for line in (root / "evolution_history.jsonl")
                    .read_text(encoding="utf-8")
                    .splitlines()
                    if line.strip()
                ][-1]
            )
    assert report["knowledge_recall"]["basis"] == RECALL_BASIS_NOT_CONSULTED
    assert report["knowledge_recall"]["lesson_ids"] == []
    assert recall_not_consulted().lessons == ()


def _run_curriculum_node() -> ast.FunctionDef:
    for node in ast.walk(ast.parse(RUNNER_SOURCE)):
        if isinstance(node, ast.FunctionDef) and node.name == "run_curriculum":
            return node
    raise AssertionError("run_curriculum is gone from the runner")


def test_run_curriculum_feeds_the_advisor_the_built_context() -> None:
    calls = [
        node
        for node in ast.walk(_run_curriculum_node())
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "propose_evolution_advice"
    ]
    assert calls, "the runner no longer asks for advice"
    argument = calls[0].args[0]
    assert isinstance(argument, ast.Call) and isinstance(argument.func, ast.Name), (
        "the advisor context is not built by a named function"
    )
    assert argument.func.id == "build_advisor_context"
    assert "recall" in {keyword.arg for keyword in argument.keywords}, (
        "the advisor context is built without the recalled lessons"
    )


def test_run_curriculum_records_the_recall_for_the_report() -> None:
    written = [
        node
        for node in ast.walk(_run_curriculum_node())
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Subscript)
            and isinstance(target.value, ast.Name)
            and target.value.id == "evolution"
            and isinstance(target.slice, ast.Constant)
            and target.slice.value == "knowledge_recall"
            for target in node.targets
        )
    ]
    assert written, 'run_curriculum does not store evolution["knowledge_recall"]'
