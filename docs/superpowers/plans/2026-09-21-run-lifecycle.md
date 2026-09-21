# Run lifecycle implementation plan

1. Add failing tests for a timed-out engine, an unexpected engine exit, and simulated Ctrl+C cancellation.
2. Replace blocking `subprocess.run` execution with one process-group-aware `Popen` lifecycle.
3. Terminate, then kill after a short drain deadline; always collect output and finalize partial evidence.
4. Persist lifecycle, finish time, and engine exit status in both run metadata and the result.
5. Run the full suite and a real-k6 timeout experiment, update documentation, and commit.
