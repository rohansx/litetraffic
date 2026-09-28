import type { JourneyFunnel } from "@/api/types";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { formatNumber } from "@/lib/format";

export function Journeys({ journeys }: { journeys: Record<string, JourneyFunnel> }) {
  return (
    <div className="grid grid-cols-1 gap-4">
      {Object.entries(journeys).map(([name, funnel]) => (
        <JourneyCard key={name} name={name} funnel={funnel} />
      ))}
    </div>
  );
}

function JourneyCard({ name, funnel }: { name: string; funnel: JourneyFunnel }) {
  const stalled = funnel.stalled.filter((item) => item.count > 0);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-mono text-sm">{name}</CardTitle>
        <CardDescription>{formatNumber(funnel.started)} journeys started.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-4">
        <ol aria-label={`${name} stages`} className="grid gap-2">
          {funnel.stages.map((stage) => {
            const share = funnel.started > 0 ? Math.min(stage.reached / funnel.started, 1) : 0;
            return (
              <li key={stage.name} className="grid grid-cols-[minmax(0,10rem)_1fr_auto] items-center gap-3 text-sm">
                <span className="truncate font-mono" title={stage.name}>
                  {stage.name}
                </span>
                <div
                  role="meter"
                  aria-label={`${stage.name} reached`}
                  aria-valuemin={0}
                  aria-valuemax={funnel.started}
                  aria-valuenow={stage.reached}
                  className="h-2 overflow-hidden rounded-full bg-muted"
                >
                  <div className="h-full rounded-full bg-primary" style={{ width: `${share * 100}%` }} />
                </div>
                <span className="tabular-nums text-muted-foreground">
                  {formatNumber(stage.reached)} / {formatNumber(funnel.started)}
                </span>
              </li>
            );
          })}
        </ol>
        {stalled.length > 0 && (
          <ul aria-label={`${name} stalls`} className="grid gap-1 text-sm text-muted-foreground">
            {stalled.map((item) => (
              <li key={item.after}>
                <span className="tabular-nums text-foreground">{formatNumber(item.count)}</span> stalled after{" "}
                <span className="font-mono">{item.after}</span>
              </li>
            ))}
          </ul>
        )}
        {funnel.undeclared.length > 0 && (
          <p className="text-sm text-muted-foreground">
            Undeclared stages reported: <span className="font-mono">{funnel.undeclared.join(", ")}</span>
          </p>
        )}
      </CardContent>
    </Card>
  );
}
