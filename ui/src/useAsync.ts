import { useEffect, useState } from "react";

export type AsyncState<T> =
  | { status: "loading"; data?: T }
  | { status: "ok"; data: T }
  | { status: "error"; error: Error; data?: T };

type Settled<T> = { key: string } & ({ ok: true; data: T } | { ok: false; error: Error });

/** Run `load` whenever `key` changes; stale responses are ignored, the last data stays visible. */
export function useAsync<T>(load: () => Promise<T>, key: string): AsyncState<T> {
  const [settled, setSettled] = useState<Settled<T> | null>(null);
  const [lastData, setLastData] = useState<{ data: T } | null>(null);
  useEffect(() => {
    let live = true;
    load().then(
      (data) => {
        if (!live) return;
        setSettled({ key, ok: true, data });
        setLastData({ data });
      },
      (error: unknown) => {
        if (!live) return;
        setSettled({ key, ok: false, error: error instanceof Error ? error : new Error(String(error)) });
      },
    );
    return () => {
      live = false;
    };
    // `key` captures everything `load` depends on.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  if (settled === null || settled.key !== key) {
    return lastData ? { status: "loading", data: lastData.data } : { status: "loading" };
  }
  if (settled.ok) return { status: "ok", data: settled.data };
  return lastData ? { status: "error", error: settled.error, data: lastData.data } : { status: "error", error: settled.error };
}
