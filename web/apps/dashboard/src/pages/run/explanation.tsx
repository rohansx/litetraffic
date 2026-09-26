import { useState } from "react";
import { LoaderCircle, Sparkles } from "lucide-react";
import { api } from "@/api/client";
import type { AiExplanation, Explanation } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { formatDateTime } from "@/lib/format";
import { useApi } from "@/lib/use-api";

export function ExplanationCard({ runId, explanation, cached }: { runId: string; explanation: Explanation; cached?: AiExplanation }) {
  const meta = useApi("meta", (signal) => api.meta(signal));
  const [ai, setAi] = useState(cached);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string>();
  const cli = meta.data?.explain_cli;

  async function explain() {
    setPending(true);
    setError(undefined);
    try {
      setAi(await api.explain(runId));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setPending(false);
    }
  }

  return (
    <Card className="overflow-hidden pt-0">
      <div className="window-card-header">
        <span className="truncate">What happened</span>
      </div>
      <CardContent className="grid gap-5">
        <p className="text-lg font-medium text-balance">{explanation.headline}</p>
        <div className="grid gap-5 md:grid-cols-2">
          {explanation.sections.map((section) => (
            <section key={section.title} aria-label={section.title}>
              <h3 className="mb-2 font-mono text-xs uppercase tracking-wider text-muted-foreground">{section.title}</h3>
              <ul className="grid list-disc gap-1.5 pl-5 text-sm">
                {section.items.map((item) => (
                  <li key={item} className="break-words">{item}</li>
                ))}
              </ul>
            </section>
          ))}
        </div>

        <div className="grid gap-3 border-t pt-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="text-sm">
              <p className="font-medium">AI explanation</p>
              <p className="text-muted-foreground">
                {ai
                  ? `Written by ${ai.cli} · ${formatDateTime(ai.created_at)}`
                  : cli
                    ? `Uses your local ${cli} CLI. It sends this run's result and observations to its model.`
                    : "Install the claude or codex CLI to get a written explanation."}
              </p>
            </div>
            <Button variant="outline" onClick={explain} disabled={!cli || pending}>
              {pending ? <LoaderCircle className="animate-spin" aria-hidden /> : <Sparkles aria-hidden />}
              {pending ? "Explaining…" : ai ? "Explain again" : "Explain with AI"}
            </Button>
          </div>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
          {ai && <p className="whitespace-pre-wrap rounded-md bg-muted/50 p-4 text-sm leading-relaxed">{ai.text}</p>}
        </div>
      </CardContent>
    </Card>
  );
}
