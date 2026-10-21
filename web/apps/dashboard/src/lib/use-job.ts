import { useEffect, useState } from "react";
import { api, ApiError } from "@/api/client";
import type { Job } from "@/api/types";

/** Mutable so tests can poll fast. */
export const JOB_POLL = { ms: 1000 };

/** Poll one job every second while it runs; stop as soon as it is not running (or the server rejects the request). */
export function useJob(id: string | null | undefined): { job?: Job; error?: Error } {
  const [state, setState] = useState<{ id?: string; job?: Job; error?: Error }>({});

  useEffect(() => {
    if (!id) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const job = await api.job(id, controller.signal);
        setState({ id, job });
        if (job.state === "running") timer = setTimeout(tick, JOB_POLL.ms);
      } catch (caught) {
        if (controller.signal.aborted) return;
        const error = caught instanceof Error ? caught : new Error(String(caught));
        setState((prev) => ({ id, job: prev.id === id ? prev.job : undefined, error }));
        // A transient network error keeps polling; an answer from the server (404, 500) does not.
        if (!(caught instanceof ApiError)) timer = setTimeout(tick, JOB_POLL.ms * 2);
      }
    };
    void tick();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id]);

  const current = state.id === id;
  return { job: current ? state.job : undefined, error: current ? state.error : undefined };
}

/** Seconds as m:ss (h:mm:ss past an hour). */
export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(totalSeconds));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}
