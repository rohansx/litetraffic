import { Link } from "react-router";
import type { Activity } from "@/api/types";
import { Facts } from "@/components/facts";
import { EmptyState } from "@/components/states";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDateTime, formatNumber } from "@/lib/format";

/** A background `up` activity: repeated slices of traffic, deliberately without a verdict. */
export function ActivityView({ activity }: { activity: Activity }) {
  return (
    <>
      <Card>
        <CardContent>
          <Facts
            items={[
              ["Status", <span className={activity.status === "error" ? "text-fail" : undefined}>{activity.status}</span>],
              ["Target", <span className="font-mono text-xs" title={activity.target}>{activity.target}</span>],
              ["Starting seed", activity.starting_seed],
              ["Slices", activity.max_slices == null ? `${activity.slices.length} (no limit)` : `${activity.slices.length} of ${activity.max_slices}`],
              ["Started", formatDateTime(activity.started_at)],
              ["Finished", formatDateTime(activity.finished_at)],
              ["Scenario digest", <span className="font-mono text-xs" title={activity.scenario_sha256}>{activity.scenario_sha256.slice(0, 12)}</span>],
            ]}
          />
          {activity.error && <p className="mt-4 text-sm text-fail">{activity.error}</p>}
        </CardContent>
      </Card>
      <Card>
        <CardHeader>
          <CardTitle className="font-display text-lg font-semibold">Slices</CardTitle>
          <CardDescription>Background traffic has no verdict. Each slice is an ordinary run; open one for its evidence.</CardDescription>
        </CardHeader>
        <CardContent>
          {activity.slices.length ? (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Run</TableHead>
                  <TableHead className="text-right">Seed</TableHead>
                  <TableHead>Lifecycle</TableHead>
                  <TableHead className="text-right">Iterations</TableHead>
                  <TableHead className="text-right">HTTP requests</TableHead>
                  <TableHead>Finished</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {activity.slices.map((slice) => (
                  <TableRow key={slice.run_id}>
                    <TableCell className="font-mono text-xs">
                      <Link to={`/runs/${encodeURIComponent(slice.run_id)}`} className="hover:underline focus-visible:underline">
                        {slice.run_id}
                      </Link>
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{slice.seed}</TableCell>
                    <TableCell className="text-muted-foreground">{slice.lifecycle.replace("_", " ")}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatNumber(slice.iterations)}</TableCell>
                    <TableCell className="text-right tabular-nums">{formatNumber(slice.http_reqs)}</TableCell>
                    <TableCell className="text-muted-foreground">{formatDateTime(slice.finished_at)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          ) : (
            <EmptyState title="No slices yet">The first slice appears here once it finishes.</EmptyState>
          )}
        </CardContent>
      </Card>
    </>
  );
}
