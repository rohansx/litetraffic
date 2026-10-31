import { cn } from "@/lib/utils";

/** A saved or generated file, read-only: scrollable and focusable so keyboard users can read long files. */
export function FileView({ path, content, className }: { path: string; content: string; className?: string }) {
  return (
    <figure className={cn("min-w-0 overflow-hidden rounded-md border", className)}>
      <figcaption className="border-b bg-muted px-3 py-1.5 font-mono text-xs text-muted-foreground">{path}</figcaption>
      <pre
        tabIndex={0}
        aria-label={`Contents of ${path}`}
        className="max-h-96 overflow-auto bg-card p-3 font-mono text-xs leading-relaxed whitespace-pre focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        {content}
      </pre>
    </figure>
  );
}
