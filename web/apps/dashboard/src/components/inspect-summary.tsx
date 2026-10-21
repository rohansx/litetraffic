import type { Inspect } from "@/api/types";
import { Facts } from "@/components/facts";
import { formatNumber } from "@/lib/format";

/** What a test will do, from `litetraffic inspect --json`: plans, ceilings, schedule, checks. */
export function InspectSummary({ inspect }: { inspect: Inspect }) {
  const seconds = inspect.resolved_schedule.reduce((sum, phase) => sum + phase.seconds, 0);
  return (
    <div className="grid gap-4">
      <Facts
        items={[
          ["Planned journeys", formatNumber(inspect.planned_journeys)],
          ["Max requests", formatNumber(inspect.maximum_journey_requests + inspect.maximum_observation_requests)],
          ["Max writes", formatNumber(inspect.maximum_journey_writes)],
          ["Schedule", `${formatNumber(seconds)} s in ${inspect.resolved_schedule.length} phases`],
        ]}
      />
      <section aria-label="Schedule phases">
        <h3 className="mb-1 text-sm font-medium">Schedule phases</h3>
        <ul className="grid gap-1 font-mono text-xs text-muted-foreground">
          {inspect.resolved_schedule.map((phase) => (
            <li key={phase.name}>
              {phase.name}: {phase.rate} journeys/s for {phase.seconds} s
            </li>
          ))}
        </ul>
      </section>
      <section aria-label="Assertions">
        <h3 className="mb-1 text-sm font-medium">Checks ({inspect.assertions.length})</h3>
        {inspect.assertions.length ? (
          <ul className="flex flex-wrap gap-1.5">
            {inspect.assertions.map((name) => (
              <li key={name} className="rounded-md bg-muted px-2 py-0.5 font-mono text-xs">
                {name}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted-foreground">This test declares no checks.</p>
        )}
      </section>
      {inspect.observations.length > 0 && (
        <section aria-label="Observations">
          <h3 className="mb-1 text-sm font-medium">Observations ({inspect.observations.length})</h3>
          <ul className="grid gap-1 font-mono text-xs text-muted-foreground">
            {inspect.observations.map((observation) => (
              <li key={`${observation.assertion}:${observation.path}`} className="wrap-anywhere">
                {observation.assertion} reads {observation.path}
              </li>
            ))}
          </ul>
        </section>
      )}
      {inspect.fixture.command && (
        <section aria-label="Setup command">
          <h3 className="mb-1 text-sm font-medium">Setup command</h3>
          <p className="font-mono text-xs break-all text-muted-foreground">{inspect.fixture.command.setup.join(" ")}</p>
        </section>
      )}
    </div>
  );
}
