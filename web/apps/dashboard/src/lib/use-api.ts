import { useCallback, useEffect, useRef, useState } from "react";

interface State<T> {
  key?: string;
  data?: T;
  error?: Error;
}

/**
 * Load `key`'s data once, and again on `reload()`. Data from a previous key is never shown;
 * data for the same key stays visible while a reload is in flight (auto-refresh does not flash).
 */
export function useApi<T>(key: string, load: (signal: AbortSignal) => Promise<T>) {
  const [state, setState] = useState<State<T>>({});
  const [tick, setTick] = useState(0);
  const loadRef = useRef(load);
  loadRef.current = load;

  useEffect(() => {
    const controller = new AbortController();
    loadRef.current(controller.signal).then(
      (data) => setState({ key, data }),
      (caught: unknown) => {
        if (controller.signal.aborted) return;
        const error = caught instanceof Error ? caught : new Error(String(caught));
        setState((prev) => ({ key, data: prev.key === key ? prev.data : undefined, error }));
      },
    );
    return () => controller.abort();
  }, [key, tick]);

  const current = state.key === key;
  return {
    data: current ? state.data : undefined,
    error: current ? state.error : undefined,
    loading: !current,
    reload: useCallback(() => setTick((value) => value + 1), []),
  };
}
