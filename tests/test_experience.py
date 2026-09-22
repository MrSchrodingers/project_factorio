from pathlib import Path

from factorio_ai_lab.learning.experience import (
    CounterexampleRecord,
    ExperienceBuffer,
    counterexample_signature,
)


def test_experience_buffer_deduplicates_same_run_signature(tmp_path: Path) -> None:
    buffer = ExperienceBuffer(tmp_path / "counterexamples.jsonl")
    diagnostics = {"route_deficits": ["iron=10/20"]}
    signature = counterexample_signature(
        stage="Electric mining transition",
        phase="route_buffer",
        detail="short",
        diagnostics=diagnostics,
    )
    record = CounterexampleRecord(
        run_id="run-1",
        stage="Electric mining transition",
        phase="route_buffer",
        detail="short",
        signature=signature,
        diagnostics=diagnostics,
        configuration={"autonomy_belt_margin": 6},
    )
    assert buffer.append(record)
    assert not buffer.append(record)
    assert buffer.signature_count(signature) == 1


def test_experience_buffer_returns_stage_scoped_history(tmp_path: Path) -> None:
    buffer = ExperienceBuffer(tmp_path / "counterexamples.jsonl")
    for index, stage in enumerate(("A", "B", "A")):
        diagnostics = {"index": index}
        signature = counterexample_signature(
            stage=stage,
            phase="p",
            detail=str(index),
            diagnostics=diagnostics,
        )
        assert buffer.append(
            CounterexampleRecord(
                run_id=f"run-{index}",
                stage=stage,
                phase="p",
                detail=str(index),
                signature=signature,
                diagnostics=diagnostics,
                configuration={},
            )
        )
    assert [row["run_id"] for row in buffer.recent(stage="A")] == [
        "run-0",
        "run-2",
    ]
