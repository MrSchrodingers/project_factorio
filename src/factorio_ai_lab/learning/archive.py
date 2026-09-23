"""
Quality-diversity archive of behavioural niches for factory genomes.

The selection loop this serves keeps one global incumbent and one challenger
per generation, and only accepts a challenger that beats that incumbent. Over
the 36 generations in runs/evolution_history.jsonl it promoted twice. That is
what a population of one produces: a random walk with conditional acceptance,
because nothing keeps the variation that selection would have to act on.

This archive keeps one elite per behaviour, in the MAP-Elites sense. A genome
competes only against genomes that behave like it, so a lineage that is worse
overall but better at its own way of running survives instead of being erased
by the global champion, and the parent of the next mutation is drawn from the
archive rather than always being that champion.

Two rules carry the design:

* No scalar fitness is invented here. Inside a niche the incumbent question is
  decided by ``survival.compare_challenger``, whose ``_commensurate`` guard
  exists because rates measured under different protocols are not comparable.
  Collapsing those metrics into one number would restore exactly the illegal
  comparison that guard was written to block.
* Evidence that was not measured is never banded. A candidate whose behaviour
  cannot be computed from what was recorded is refused, with a readable reason,
  and the refusal is counted and queryable. Absence stays absence.

The module depends only on ``survival`` and the standard library, so the
runner can import it without an import cycle.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Any

from factorio_ai_lab.learning.survival import (
    FitnessVector,
    PromotionDecision,
    compare_challenger,
)

#: Payload layout written by save()/to_dict(). Bump it whenever the descriptor
#: bins change: an archive binned by other edges describes different niches,
#: and reading it as if it were this one would silently merge lineages.
ARCHIVE_SCHEMA_VERSION = "niche_archive_v1"

#: Same default as compare_challenger, kept explicit so a loaded archive
#: restores the floor it was built with instead of the current default.
DEFAULT_RETENTION_RATIO = 0.80

#: Why a candidate produced no descriptor.
#:
#: The endogenous/intervention split is None whenever any rate key has unknown
#: provenance, which is the state of every generation recorded before
#: rate_sources existed. Banding it would mean reading "not measured" as
#: "measured zero" and filing a factory under an autonomy it never showed.
REFUSAL_UNCLASSIFIED_PROVENANCE = "unclassified_rate_provenance"

#: No rate was measured at all, so the endogenous share is 0/0. An undefined
#: fraction is not zero autonomy: a genome that produced nothing has not
#: demonstrated manual production either, and filing it in the lowest autonomy
#: band would let it compete with, and displace, factories that were measured.
REFUSAL_NO_MEASURED_PRODUCTION = "no_measured_production"

#: Outcome of insert().
INSERTION_FOUNDED = "founded"
INSERTION_REPLACED = "replaced"
INSERTION_KEPT = "kept"
INSERTION_REFUSED = "refused"

#: Autonomy bins over endogenous / (endogenous + intervention).
#:
#: Quartiles, left-closed, with the top bin closed on the right so that a
#: perfectly endogenous factory (1.0) shares a bin with a nearly endogenous
#: one instead of owning a niche it can never be challenged in. The axis is
#: deliberately coarse: the split is a ratio of two sums of noisy rates, and
#: finer bins would separate lineages on measurement noise, which fragments
#: the archive into niches of one and defeats the point of keeping elites.
AUTONOMY_BAND_EDGES: tuple[float, ...] = (0.25, 0.50, 0.75)
AUTONOMY_BANDS: tuple[str, ...] = (
    "autonomy_00_25",
    "autonomy_25_50",
    "autonomy_50_75",
    "autonomy_75_100",
)

#: Capability bins over the count this genome built itself, that is
#: ``capabilities - inherited_capabilities``.
#:
#: Edges are counts, with an open top bin. They follow the counts the loop
#: actually produced in runs/evolution_history.jsonl: 0, 1, 3, 4, 6, 7 and 8,
#: with 7 appearing in 27 of the 36 recorded generations. A plateau that dense
#: must not be split by one capability of difference, so everything from 7 up
#: shares the top bin and the other two axes do the separating there; the
#: sparse low counts keep pair-width bins so an early lineage still has a
#: niche of its own to be elite of.
CAPABILITY_BAND_EDGES: tuple[int, ...] = (1, 3, 5, 7)
CAPABILITY_BANDS: tuple[str, ...] = (
    "built_0",
    "built_1_2",
    "built_3_4",
    "built_5_6",
    "built_7_plus",
)


class ArchiveSchemaError(ValueError):
    """
    A stored archive could not be read as this schema.

    Raised instead of returning an empty archive: a silent empty read would
    discard every elite on disk and restart the search from nothing while
    reporting success.
    """


@dataclass(frozen=True)
class BehaviorDescriptor:
    """
    The niche a genome belongs to.

    ``halt_cause`` is stored as it was recorded, None included. None means no
    entity status was observed (see factory_graph.classify_halt_cause), which
    is a different fact from any named cause and from a factory that halted
    for no observed reason, so it is its own niche rather than being folded
    into an "unknown" bucket.

    Niching by failure mode is the point of the axis: the archive then holds
    the best solution found for each way of dying, which is exactly the
    information a single global champion destroys.
    """

    autonomy_band: str
    capability_band: str
    halt_cause: str | None

    @property
    def sort_key(self) -> tuple[str, str, int, str]:
        """Total order for reproducible iteration and sampling."""
        return (
            self.autonomy_band,
            self.capability_band,
            0 if self.halt_cause is None else 1,
            self.halt_cause or "",
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "autonomy_band": self.autonomy_band,
            "capability_band": self.capability_band,
            "halt_cause": self.halt_cause,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> BehaviorDescriptor:
        autonomy = payload.get("autonomy_band")
        capability = payload.get("capability_band")
        halt_cause = payload.get("halt_cause")
        if autonomy not in AUTONOMY_BANDS:
            raise ArchiveSchemaError(
                f"unknown autonomy band {autonomy!r}; this build bins autonomy "
                f"as {list(AUTONOMY_BANDS)}"
            )
        if capability not in CAPABILITY_BANDS:
            raise ArchiveSchemaError(
                f"unknown capability band {capability!r}; this build bins "
                f"capabilities as {list(CAPABILITY_BANDS)}"
            )
        if halt_cause is not None and not isinstance(halt_cause, str):
            raise ArchiveSchemaError(
                f"halt_cause must be a string or null, got {halt_cause!r}"
            )
        return cls(
            autonomy_band=autonomy,
            capability_band=capability,
            halt_cause=halt_cause,
        )


@dataclass(frozen=True)
class DescriptorRefusal:
    """Why a candidate could not be placed, in words a reader can act on."""

    reason: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"reason": self.reason, "detail": self.detail}


@dataclass(frozen=True)
class RefusalRecord:
    """A refused candidate, kept so absence stays auditable."""

    reason: str
    detail: str
    record: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "detail": self.detail,
            "record": dict(self.record),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> RefusalRecord:
        record = payload.get("record")
        return cls(
            reason=str(payload.get("reason", "")),
            detail=str(payload.get("detail", "")),
            record=dict(record) if isinstance(record, Mapping) else {},
        )


@dataclass(frozen=True)
class ArchiveEntry:
    """The elite of one niche, with the provenance needed to breed from it."""

    descriptor: BehaviorDescriptor
    fitness: FitnessVector
    record: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "descriptor": self.descriptor.to_dict(),
            "fitness": self.fitness.to_dict(),
            "record": dict(self.record),
        }


@dataclass
class NicheStats:
    """
    How contested a niche has been.

    ``contests`` counts candidates that landed on the niche while it was
    already occupied; founding it is not a contest, there was nothing to
    dispute. ``replacements`` counts the contests that changed the elite. A
    niche with many contests and no replacement is a stagnated lineage, which
    is the state a single scalar champion never lets anyone see.
    """

    contests: int = 0
    replacements: int = 0

    def to_dict(self) -> dict[str, int]:
        return {"contests": self.contests, "replacements": self.replacements}


@dataclass(frozen=True)
class InsertionResult:
    """Outcome of one insert(), including the verdict that produced it."""

    status: str
    descriptor: BehaviorDescriptor | None = None
    refusal: DescriptorRefusal | None = None
    decision: PromotionDecision | None = None
    elite: ArchiveEntry | None = None
    displaced: ArchiveEntry | None = None

    @property
    def accepted(self) -> bool:
        return self.status in (INSERTION_FOUNDED, INSERTION_REPLACED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "accepted": self.accepted,
            "descriptor": (
                None if self.descriptor is None else self.descriptor.to_dict()
            ),
            "refusal": None if self.refusal is None else self.refusal.to_dict(),
            "decision": None if self.decision is None else self.decision.to_dict(),
        }


def _band(value: float, edges: tuple[float, ...], labels: tuple[str, ...]) -> str:
    """Left-closed binning: an edge belongs to the bin it opens."""
    return labels[bisect_right(edges, value)]


def built_capability_count(fitness: FitnessVector) -> int:
    """
    Capabilities this genome built, excluding what it inherited.

    An heir starts holding its ancestor's capabilities; counting those would
    file every first generation of an inheritance in the top band and report
    inheritance as achievement. ``inherited_capabilities`` of None means no
    inheritance was in play, so nothing is subtracted.
    """
    inherited = fitness.inherited_capabilities or frozenset()
    return len(set(fitness.capabilities) - set(inherited))


def behavior_descriptor(
    fitness: FitnessVector,
) -> BehaviorDescriptor | DescriptorRefusal:
    """
    Place a fitness in a niche, or refuse to place it.

    Every axis reads a field the fitness vector already carries; nothing is
    derived from a metric that is usually absent. When the endogenous share
    cannot be computed the answer is a refusal, never a fabricated band.
    """
    endogenous = fitness.endogenous_rate_per_s
    intervention = fitness.intervention_rate_per_s
    if endogenous is None or intervention is None:
        unknown = fitness.unclassified_rate_keys
        listed = ", ".join(unknown) if unknown else "(none recorded)"
        return DescriptorRefusal(
            reason=REFUSAL_UNCLASSIFIED_PROVENANCE,
            detail=(
                "endogenous share is undefined: rate provenance is unknown for "
                f"{listed}. The fitness predates rate_sources or the stage did "
                "not classify its rates, so no autonomy band can be assigned "
                "without inventing the measurement."
            ),
        )
    total = endogenous + intervention
    if total <= 0.0:
        return DescriptorRefusal(
            reason=REFUSAL_NO_MEASURED_PRODUCTION,
            detail=(
                "endogenous share is 0/0: no measured rate is positive, so the "
                "genome demonstrated neither automated nor manual production."
            ),
        )
    return BehaviorDescriptor(
        autonomy_band=_band(endogenous / total, AUTONOMY_BAND_EDGES, AUTONOMY_BANDS),
        capability_band=_band(
            built_capability_count(fitness),
            CAPABILITY_BAND_EDGES,
            CAPABILITY_BANDS,
        ),
        halt_cause=fitness.halt_cause,
    )


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    """
    Write JSON through a temporary file in the same directory, then rename.

    Same shape as ``atomic_json`` in experiments/curriculum_runner.py, kept
    local on purpose: importing the runner from a learning module would close
    an import cycle, since the runner imports this one.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


class NicheArchive:
    """
    One elite per behavioural niche, plus the ledger of what it refused.

    Not thread-safe and not process-safe: two runners inserting into the same
    file would each save a whole archive and the later write would win. The
    evolution loop runs one generation at a time, which is the assumption this
    relies on.
    """

    def __init__(self, *, retention_ratio: float = DEFAULT_RETENTION_RATIO) -> None:
        if not 0 < retention_ratio <= 1:
            raise ValueError("retention_ratio must be in (0, 1]")
        self._retention_ratio = float(retention_ratio)
        self._niches: dict[BehaviorDescriptor, ArchiveEntry] = {}
        self._stats: dict[BehaviorDescriptor, NicheStats] = {}
        self._refusals: list[RefusalRecord] = []

    def __len__(self) -> int:
        return len(self._niches)

    @property
    def retention_ratio(self) -> float:
        return self._retention_ratio

    def descriptors(self) -> tuple[BehaviorDescriptor, ...]:
        """Occupied niches in a stable order, so callers stay reproducible."""
        return tuple(sorted(self._niches, key=lambda item: item.sort_key))

    def entries(self) -> tuple[ArchiveEntry, ...]:
        return tuple(self._niches[key] for key in self.descriptors())

    def elite(self, descriptor: BehaviorDescriptor) -> ArchiveEntry | None:
        return self._niches.get(descriptor)

    def stats(self, descriptor: BehaviorDescriptor) -> NicheStats:
        stored = self._stats.get(descriptor, NicheStats())
        return NicheStats(contests=stored.contests, replacements=stored.replacements)

    def refusals(self) -> tuple[RefusalRecord, ...]:
        """Every candidate the archive declined, in arrival order."""
        return tuple(self._refusals)

    def refusal_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self._refusals:
            counts[entry.reason] = counts.get(entry.reason, 0) + 1
        return counts

    def insert(
        self,
        fitness: FitnessVector,
        record: Mapping[str, Any] | None = None,
    ) -> InsertionResult:
        """
        Offer a candidate to its niche.

        An empty niche accepts it. An occupied one runs the same
        incumbent-preserving comparison the global champion uses, against the
        elite of that niche only, and keeps the elite unless the candidate is
        promoted. ``record`` is stored verbatim next to the fitness; the
        configuration it carries is what a caller needs to breed from a parent.
        """
        stored_record = dict(record) if record else {}
        described = behavior_descriptor(fitness)
        if isinstance(described, DescriptorRefusal):
            self._refusals.append(
                RefusalRecord(
                    reason=described.reason,
                    detail=described.detail,
                    record=stored_record,
                )
            )
            return InsertionResult(status=INSERTION_REFUSED, refusal=described)

        candidate = ArchiveEntry(
            descriptor=described, fitness=fitness, record=stored_record
        )
        statistics = self._stats.setdefault(described, NicheStats())
        incumbent = self._niches.get(described)
        if incumbent is None:
            self._niches[described] = candidate
            return InsertionResult(
                status=INSERTION_FOUNDED, descriptor=described, elite=candidate
            )

        statistics.contests += 1
        decision = compare_challenger(
            incumbent.fitness,
            fitness,
            retention_ratio=self._retention_ratio,
        )
        if not decision.promoted:
            return InsertionResult(
                status=INSERTION_KEPT,
                descriptor=described,
                decision=decision,
                elite=incumbent,
            )
        statistics.replacements += 1
        self._niches[described] = candidate
        return InsertionResult(
            status=INSERTION_REPLACED,
            descriptor=described,
            decision=decision,
            elite=candidate,
            displaced=incumbent,
        )

    def sample_parent(self, rng: Random) -> ArchiveEntry | None:
        """
        Draw the parent of the next mutation, uniformly over occupied niches.

        Uniform over niches rather than over quality is the canonical
        MAP-Elites choice: the archive exists to spend generations on
        behaviours that are under-explored, and weighting by fitness would put
        the global champion back at the centre of the search.

        The draw consumes ``rng`` and nothing else, so a run reproduces from
        its seed. An empty archive answers None and the caller keeps whatever
        parent selection it used before.
        """
        occupied = self.descriptors()
        if not occupied:
            return None
        return self._niches[rng.choice(occupied)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ARCHIVE_SCHEMA_VERSION,
            "retention_ratio": self._retention_ratio,
            "niches": [
                {
                    **self._niches[key].to_dict(),
                    **self.stats(key).to_dict(),
                }
                for key in self.descriptors()
            ],
            "refusals": [entry.to_dict() for entry in self._refusals],
            # Derived from "refusals" and recomputed on load. Written for a
            # reader of the file, never read back as a second source of truth.
            "refusal_counts": self.refusal_counts(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> NicheArchive:
        if not isinstance(payload, Mapping):
            raise ArchiveSchemaError(
                f"archive payload must be a JSON object, got {type(payload).__name__}"
            )
        version = payload.get("schema_version")
        if version != ARCHIVE_SCHEMA_VERSION:
            raise ArchiveSchemaError(
                f"unsupported archive schema {version!r}; this build reads "
                f"{ARCHIVE_SCHEMA_VERSION!r}. Refusing to continue: reading it "
                "as an empty archive would discard every elite it holds and "
                "restart the search while reporting success."
            )
        ratio_raw = payload.get("retention_ratio", DEFAULT_RETENTION_RATIO)
        if not isinstance(ratio_raw, (int, float)) or not 0 < float(ratio_raw) <= 1:
            raise ArchiveSchemaError(
                f"retention_ratio must be a number in (0, 1], got {ratio_raw!r}"
            )
        archive = cls(retention_ratio=float(ratio_raw))

        niches = payload.get("niches", [])
        if not isinstance(niches, (list, tuple)):
            raise ArchiveSchemaError('"niches" must be a list')
        for row in niches:
            if not isinstance(row, Mapping):
                raise ArchiveSchemaError('every entry in "niches" must be an object')
            descriptor_raw = row.get("descriptor")
            fitness_raw = row.get("fitness")
            if not isinstance(descriptor_raw, Mapping):
                raise ArchiveSchemaError("a niche entry has no descriptor object")
            if not isinstance(fitness_raw, Mapping):
                raise ArchiveSchemaError("a niche entry has no fitness object")
            descriptor = BehaviorDescriptor.from_dict(descriptor_raw)
            record = row.get("record")
            archive._niches[descriptor] = ArchiveEntry(
                descriptor=descriptor,
                fitness=FitnessVector.from_dict(fitness_raw),
                record=dict(record) if isinstance(record, Mapping) else {},
            )
            archive._stats[descriptor] = NicheStats(
                contests=int(row.get("contests", 0) or 0),
                replacements=int(row.get("replacements", 0) or 0),
            )

        refusals = payload.get("refusals", [])
        if not isinstance(refusals, (list, tuple)):
            raise ArchiveSchemaError('"refusals" must be a list')
        archive._refusals = [
            RefusalRecord.from_dict(row)
            for row in refusals
            if isinstance(row, Mapping)
        ]
        return archive

    def save(self, path: Path | str) -> None:
        _atomic_json(Path(path), self.to_dict())

    @classmethod
    def load(
        cls,
        path: Path | str,
        *,
        retention_ratio: float = DEFAULT_RETENTION_RATIO,
    ) -> NicheArchive:
        """
        Read an archive, or start an empty one when the file does not exist.

        A missing file is a first run and answers an empty archive. A file that
        exists and cannot be read raises: that is a lost archive, not an empty
        one, and the difference decides whether the search restarts.
        """
        target = Path(path)
        if not target.exists():
            return cls(retention_ratio=retention_ratio)
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ArchiveSchemaError(
                f"{target} exists but could not be read as JSON: {error}"
            ) from error
        return cls.from_dict(payload)
