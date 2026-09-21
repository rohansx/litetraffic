# Spiky profile and inventory conformance plan

1. Add failing manifest tests for deterministic spiky-profile compilation, fixed workload bounds, invalid spike placement, and frozen resolved schedules.
2. Extend the existing schedule model with one seeded `spiky` profile and feed its resolved phases through the current runner.
3. Add a bounded inventory fixture and k6 journey that issues concurrent reservation attempts and observes final inventory state.
4. Run the same scenario against atomic and deliberately overselling implementations with official k6 v2.2.0.
5. Record the positive/negative result, run the full suite, verify packaging, and commit.

Random bursts, sustained bursts, generic fixture plugins, and reporting remain outside this slice.
