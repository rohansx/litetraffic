import { CircleCheck, CircleX, TriangleAlert } from "lucide-react";
import type { DraftResult } from "@/api/types";
import { FileView } from "@/components/file-view";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { formatNumber } from "@/lib/format";

/** The outcome of an AI draft: validation, cost, the generated files (read-only) and the review warning. */
export function DraftResultView({ result }: { result: DraftResult }) {
  const entries = Object.entries(result.files);
  return (
    <div className="grid gap-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        {result.valid ? (
          <Badge className="gap-1.5 bg-pass-muted text-pass">
            <CircleCheck aria-hidden />
            Validates
          </Badge>
        ) : (
          <Badge className="gap-1.5 bg-fail-muted text-fail">
            <CircleX aria-hidden />
            Does not validate
          </Badge>
        )}
        <span className="text-muted-foreground">
          {result.attempts} {result.attempts === 1 ? "attempt" : "attempts"} · {formatNumber(result.tokens.input)} tokens in,{" "}
          {formatNumber(result.tokens.output)} out
        </span>
      </div>
      {result.error && (
        <Alert variant="destructive">
          <TriangleAlert aria-hidden />
          <AlertTitle>{result.valid ? "Warning" : "The draft failed"}</AlertTitle>
          <AlertDescription>
            <pre className="font-mono text-xs whitespace-pre-wrap">{result.error}</pre>
          </AlertDescription>
        </Alert>
      )}
      {entries.length > 0 && (
        <>
          <Alert className="border-inconclusive bg-inconclusive-muted text-inconclusive">
            <TriangleAlert aria-hidden />
            <AlertTitle>Review before saving: scenarios are code that runs on your machine</AlertTitle>
            <AlertDescription className="text-inconclusive">
              Read journeys.js and manifest.json below. The model wrote them from your description; you are responsible for what they send.
            </AlertDescription>
          </Alert>
          {!result.valid && <p className="text-sm text-muted-foreground">These are the files from the last attempt.</p>}
          {entries.map(([path, content]) => (
            <FileView key={path} path={path} content={content} />
          ))}
        </>
      )}
    </div>
  );
}
