import { TriangleAlert } from "lucide-react";
import type { ContainerCapture, ServerCapture } from "@/api/types";
import { EmptyState } from "@/components/states";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatNumber } from "@/lib/format";

function percent(value: number | null) {
  return value == null ? "n/a" : `${value.toFixed(1)}%`;
}

function megabytes(value: number | null) {
  return value == null ? "n/a" : `${formatNumber(value)} MiB`;
}

export function Server({ server }: { server: ServerCapture }) {
  const containers = Object.entries(server.containers);
  return (
    <div className="grid grid-cols-1 gap-4">
      {server.problems.length > 0 && (
        <ul className="grid gap-2" aria-label="Capture problems">
          {server.problems.map((text) => (
            <li key={text} className="flex gap-2 rounded-lg border bg-card p-3 text-sm">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
              {text}
            </li>
          ))}
        </ul>
      )}
      {containers.length ? (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Containers</CardTitle>
              <CardDescription>Baseline is the first docker stats sample; peak is the highest during the run.</CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Container</TableHead>
                    <TableHead className="text-right">CPU baseline</TableHead>
                    <TableHead className="text-right">CPU peak</TableHead>
                    <TableHead className="text-right">Memory baseline</TableHead>
                    <TableHead className="text-right">Memory peak</TableHead>
                    <TableHead className="text-right">Log lines</TableHead>
                    <TableHead className="text-right">Error lines</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {containers.map(([name, container]) => (
                    <TableRow key={name}>
                      <TableCell className="font-mono text-xs">
                        {name}
                        {container.truncated && (
                          <Badge variant="outline" className="ml-2">
                            truncated
                          </Badge>
                        )}
                      </TableCell>
                      <TableCell className="text-right tabular-nums">{percent(container.baseline_cpu_percent)}</TableCell>
                      <TableCell className="text-right tabular-nums">{percent(container.peak_cpu_percent)}</TableCell>
                      <TableCell className="text-right tabular-nums">{megabytes(container.baseline_mem_mb)}</TableCell>
                      <TableCell className="text-right tabular-nums">{megabytes(container.peak_mem_mb)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatNumber(container.log_lines)}</TableCell>
                      <TableCell className="text-right tabular-nums">{formatNumber(container.error_lines)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          {containers.map(([name, container]) => (
            <Signatures key={name} name={name} container={container} />
          ))}
        </>
      ) : (
        <EmptyState title="No containers captured">None of the requested containers could be captured.</EmptyState>
      )}
    </div>
  );
}

function Signatures({ name, container }: { name: string; container: ContainerCapture }) {
  if (!container.signatures.length) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="font-mono text-sm">{name}</CardTitle>
        <CardDescription>Most common error lines, with variable parts normalised.</CardDescription>
      </CardHeader>
      <CardContent>
        <Table aria-label={`Error signatures for ${name}`}>
          <TableHeader>
            <TableRow>
              <TableHead>Signature</TableHead>
              <TableHead className="text-right">Count</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {container.signatures.map((item) => (
              <TableRow key={item.signature}>
                <TableCell className="max-w-0 truncate font-mono text-xs" title={item.example}>
                  {item.signature}
                </TableCell>
                <TableCell className="text-right tabular-nums">{formatNumber(item.count)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}
