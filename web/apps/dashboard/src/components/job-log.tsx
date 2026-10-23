import { useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

/** Live log: monospace, follows the newest line unless the reader has scrolled up. */
export function JobLog({ lines, label = "Job log", className }: { lines: string[]; label?: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const pinned = useRef(true);

  useEffect(() => {
    const el = ref.current;
    if (el && pinned.current) el.scrollTop = el.scrollHeight;
  }, [lines]);

  return (
    <div
      ref={ref}
      role="log"
      aria-live="polite"
      aria-label={label}
      tabIndex={0}
      onScroll={(event) => {
        const el = event.currentTarget;
        pinned.current = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
      }}
      className={cn(
        "max-h-80 min-h-32 overflow-auto rounded-md border bg-muted p-3 font-mono text-xs leading-relaxed focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
        className,
      )}
    >
      {lines.length ? (
        lines.map((line, index) => (
          <div key={index} className="wrap-anywhere whitespace-pre-wrap">
            {line}
          </div>
        ))
      ) : (
        <p className="text-muted-foreground">Waiting for output.</p>
      )}
    </div>
  );
}
