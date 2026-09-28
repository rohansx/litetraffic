"""Per-journey stage funnel: how many journeys reached each declared stage, and where the rest stopped."""

from __future__ import annotations


def _owner(stage: str, staged: dict[str, list[str]]) -> str | None:
    owners = [name for name, stages in staged.items() if stage in stages]
    if len(owners) == 1:
        return owners[0]
    # An undeclared name can only be pinned to a journey when a single journey declares stages.
    return next(iter(staged)) if len(staged) == 1 and not owners else None


def journey_funnels(journeys, events: list[dict], iterations: object) -> tuple[dict, list[str]]:
    """Return result.json's "journeys" block (empty when no journey declares stages) and its limitations."""
    staged = {journey.name: journey.stages for journey in journeys if journey.stages}
    if not staged:
        return {}, []
    keys: dict[str, dict[str, set[str]]] = {name: {} for name in staged}
    unattributed: set[str] = set()
    for event in events:
        if event.get("type") != "stage":
            continue
        owner = _owner(event["stage"], staged)
        if owner is None:
            unattributed.add(event["stage"])
        else:
            keys[owner].setdefault(event["stage"], set()).add(event["logical_key"])
    funnels, limitations = {}, []
    for name, stages in staged.items():
        seen = keys[name]
        emitted = set().union(*seen.values()) if seen else set()
        # k6 counts iterations for the whole run; it only equals this journey's starts when it is the only journey.
        delivered = int(iterations) if len(journeys) == 1 and isinstance(iterations, (int, float)) else 0
        started = max(delivered, len(emitted))
        reached = [seen.get(stage, set()) for stage in stages]
        stalled = [{"after": "start", "count": max(started - len(reached[0]), 0)}]
        stalled += [{"after": stages[i], "count": len(reached[i] - reached[i + 1])} for i in range(len(stages) - 1)]
        undeclared = sorted(set(seen) - set(stages))
        funnels[name] = {
            "started": started,
            "stages": [{"name": stage, "reached": len(keys_)} for stage, keys_ in zip(stages, reached)],
            "stalled": stalled,
            "undeclared": undeclared,
        }
        if undeclared:
            limitations.append(f"journey {name} reported undeclared stage(s): {', '.join(undeclared)}")
    if unattributed:
        limitations.append(f"stage events match no single journey: {', '.join(sorted(unattributed))}")
    return funnels, limitations


def unfinished_journeys(funnels: dict) -> list[str]:
    """A limitation per journey where nothing reached the final stage: passing checks on unfinished work prove nothing."""
    return [
        f"No journey reached its final stage '{funnel['stages'][-1]['name']}': the checks passed on journeys that never finished."
        for funnel in funnels.values()
        if funnel["stages"][-1]["reached"] == 0
    ]
