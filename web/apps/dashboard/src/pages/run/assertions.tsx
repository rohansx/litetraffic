import { Fragment, useState } from "react";
import { ChevronRight } from "lucide-react";
import type { AssertionResult } from "@/api/types";
import { Value } from "@/components/facts";
import { EmptyState } from "@/components/states";
import { Button } from "@/components/ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { VerdictBadge } from "@/components/verdict";
import { cn } from "@/lib/utils";

const expandable = (assertion: AssertionResult) =>
  assertion.status === "fail" && Boolean(assertion.failures?.length || assertion.expected || assertion.actual);

export function AssertionsTable({ assertions }: { assertions: AssertionResult[] }) {
  const firstFailing = assertions.find(expandable)?.id;
  const [open, setOpen] = useState<string[]>(firstFailing ? [firstFailing] : []);

  if (!assertions.length) return <EmptyState title="This run declared no assertions" />;

  const failing = assertions.filter((a) => a.status === "fail").length;
  return (
    <div className="grid grid-cols-1 gap-3">
      <p className="text-sm text-muted-foreground">
        {failing ? `${failing} of ${assertions.length} assertions failed.` : `${assertions.length} assertions, none failed.`}
      </p>
      <div className="rounded-xl border bg-card">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10">
                <span className="sr-only">Details</span>
              </TableHead>
              <TableHead>Assertion</TableHead>
              <TableHead>Status</TableHead>
              <TableHead className="text-right">Samples</TableHead>
              <TableHead>Reason</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {assertions.map((assertion) => {
              const isOpen = open.includes(assertion.id);
              const panelId = `assertion-${assertion.id}`;
              return (
                <Fragment key={assertion.id}>
                  <TableRow>
                    <TableCell>
                      {expandable(assertion) && (
                        <Button
                          variant="ghost"
                          size="icon-xs"
                          aria-expanded={isOpen}
                          aria-controls={panelId}
                          aria-label={`${isOpen ? "Hide" : "Show"} failing samples for ${assertion.id}`}
                          onClick={() => setOpen((ids) => (isOpen ? ids.filter((id) => id !== assertion.id) : [...ids, assertion.id]))}
                        >
                          <ChevronRight className={cn("transition-transform", isOpen && "rotate-90")} aria-hidden />
                        </Button>
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-xs whitespace-normal break-all">{assertion.id}</TableCell>
                    <TableCell>
                      <VerdictBadge verdict={assertion.status} />
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{assertion.samples}</TableCell>
                    <TableCell className="whitespace-normal text-muted-foreground">{assertion.reason ?? ""}</TableCell>
                  </TableRow>
                  {isOpen && (
                    <TableRow className="bg-muted/40 hover:bg-muted/40">
                      <TableCell colSpan={5} id={panelId} className="whitespace-normal">
                        <FailureDetail assertion={assertion} />
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              );
            })}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

function FailureDetail({ assertion }: { assertion: AssertionResult }) {
  const failures = assertion.failures ?? [];
  return (
    <div className="grid gap-3 py-1">
      {(assertion.expected || assertion.actual) && (
        <div className="grid gap-3 md:grid-cols-2">
          <div>
            <p className="mb-1 text-xs text-muted-foreground">Expected</p>
            <Value value={assertion.expected} />
          </div>
          <div>
            <p className="mb-1 text-xs text-muted-foreground">Actual</p>
            <Value value={assertion.actual} className="text-fail" />
          </div>
        </div>
      )}
      {failures.length > 0 && (
        <>
          <p className="text-xs text-muted-foreground">First {failures.length} failing samples, expected vs actual</p>
          <ol className="grid gap-2">
            {failures.map((failure) => (
              <li key={`${failure.sequence}-${failure.logical_key}`} className="grid gap-2 rounded-lg border bg-card p-3 md:grid-cols-[12rem_1fr_1fr]">
                <div className="min-w-0 text-xs">
                  <p className="font-medium">Sample #{failure.sequence}</p>
                  <p className="truncate font-mono text-muted-foreground" title={failure.logical_key}>
                    {failure.logical_key}
                  </p>
                  {failure.detail && <p className="mt-1 text-muted-foreground">{failure.detail}</p>}
                </div>
                <div>
                  <p className="mb-1 text-xs text-muted-foreground">Expected</p>
                  <Value value={failure.expected} />
                </div>
                <div>
                  <p className="mb-1 text-xs text-muted-foreground">Actual</p>
                  <Value value={failure.actual} className="text-fail" />
                </div>
              </li>
            ))}
          </ol>
        </>
      )}
    </div>
  );
}
