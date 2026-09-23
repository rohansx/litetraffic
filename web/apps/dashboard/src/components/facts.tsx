import type { ReactNode } from "react";
import { cn } from "@/lib/utils";
import { formatValue } from "@/lib/format";

/** A definition list laid out as a responsive grid of label/value pairs. */
export function Facts({ items, className }: { items: [string, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-4", className)}>
      {items.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-xs text-muted-foreground">{label}</dt>
          <dd className="mt-0.5 truncate text-sm font-medium">{value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Any JSON value from an artifact, shown verbatim. */
export function Value({ value, className }: { value: unknown; className?: string }) {
  return (
    <pre className={cn("max-h-56 overflow-auto rounded-md bg-muted px-2 py-1.5 font-mono text-xs break-all whitespace-pre-wrap", className)}>
      {formatValue(value)}
    </pre>
  );
}
