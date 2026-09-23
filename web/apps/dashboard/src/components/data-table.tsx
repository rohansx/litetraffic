import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ChevronDown, ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Columns2 } from "lucide-react";
import { Link } from "react-router";
import {
  columnVisibilityFeature,
  createColumnHelper,
  createPaginatedRowModel,
  FlexRender,
  rowPaginationFeature,
  rowSelectionFeature,
  tableFeatures,
  useTable,
  type ColumnVisibilityState,
  type RowSelectionState,
  type Updater,
} from "@tanstack/react-table";
import type { RunListEntry } from "@/api/types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { VerdictBadge } from "@/components/verdict";
import { formatDateTime, formatRelative } from "@/lib/format";
import { cn } from "@/lib/utils";

const features = tableFeatures({
  columnVisibilityFeature,
  rowPaginationFeature,
  rowSelectionFeature,
  paginatedRowModel: createPaginatedRowModel(),
});

const column = createColumnHelper<typeof features, RunListEntry>();

/** Columns a 390px screen drops; the run cell repeats verdict, scenario and age for phones. */
const WIDE = "hidden md:table-cell";
const CELL_CLASS: Record<string, string> = {
  select: "w-10",
  scenario: WIDE,
  verdict: WIDE,
  lifecycle: `${WIDE} text-muted-foreground`,
  seed: `${WIDE} text-right tabular-nums`,
  finished: `${WIDE} text-muted-foreground`,
};
const HIDEABLE: Record<string, string> = {
  scenario: "Scenario",
  verdict: "Verdict",
  lifecycle: "Lifecycle",
  seed: "Seed",
  finished: "Finished",
};

const columns = column.columns([
  column.display({
    id: "select",
    header: () => <span className="sr-only">Select</span>,
    cell: ({ row }) =>
      row.getCanSelect() ? (
        <Checkbox
          checked={row.getIsSelected()}
          onCheckedChange={(value) => row.toggleSelected(!!value)}
          aria-label={`Select ${row.original.run_id}`}
        />
      ) : null,
    enableHiding: false,
  }),
  column.accessor("run_id", {
    id: "run",
    header: "Run",
    enableHiding: false,
    cell: ({ row: { original: run } }) => (
      <div className="font-mono text-xs">
        {run.kind === "series" ? (
          <span title="Series have no detail page">{run.run_id}</span>
        ) : (
          <Link to={`/runs/${encodeURIComponent(run.run_id)}`} className="hover:underline focus-visible:underline">
            {run.run_id}
          </Link>
        )}
        {run.kind !== "run" && (
          <Badge variant="outline" className="ml-2 font-sans">
            {run.kind}
          </Badge>
        )}
        {/* ponytail: phones keep only this cell; scenario, verdict and age stack here so nothing is clipped. */}
        <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 font-sans text-muted-foreground md:hidden">
          <VerdictBadge verdict={run.verdict} />
          <span>{run.scenario ?? "Unknown scenario"}</span>
          <span title={formatDateTime(run.finished_at)}>{formatRelative(run.finished_at)}</span>
        </div>
      </div>
    ),
  }),
  column.accessor("scenario", {
    header: "Scenario",
    cell: ({ getValue }) => {
      const scenario = getValue();
      return scenario ? (
        <Link to={`/scenarios/${encodeURIComponent(scenario)}`} className="hover:underline focus-visible:underline">
          {scenario}
        </Link>
      ) : (
        <span className="text-muted-foreground">Unknown</span>
      );
    },
  }),
  column.accessor("verdict", {
    header: "Verdict",
    cell: ({ getValue }) => <VerdictBadge verdict={getValue()} />,
  }),
  column.accessor("lifecycle", {
    header: "Lifecycle",
    cell: ({ getValue }) => getValue()?.replace("_", " ") ?? "Unknown",
  }),
  column.accessor("seed", {
    header: "Seed",
    cell: ({ getValue }) => getValue() ?? "n/a",
  }),
  column.accessor("finished_at", {
    id: "finished",
    header: "Finished",
    cell: ({ getValue }) => <span title={formatDateTime(getValue())}>{formatRelative(getValue())}</span>,
  }),
]);

export const VIEWS = [
  { value: "all", label: "All" },
  { value: "run", label: "Verify runs" },
  { value: "series", label: "Series" },
  { value: "activity", label: "Activity" },
] as const;
export type View = (typeof VIEWS)[number]["value"];

interface RunsDataTableProps {
  runs: RunListEntry[];
  view: View;
  counts: Record<View, number>;
  onViewChange: (view: View) => void;
  /** Run ids ticked for Compare, oldest tick first. */
  selected: string[];
  onSelectedChange: (ids: string[]) => void;
  /** Filter controls, shown between the view tabs and the table. */
  toolbar?: ReactNode;
  /** Shown in place of rows when the filters match nothing. */
  empty: ReactNode;
}

/** dashboard-01 DataTable adapted to runs: view tabs, column visibility, pagination, row selection for Compare. */
export function RunsDataTable({ runs, view, counts, onViewChange, selected, onSelectedChange, toolbar, empty }: RunsDataTableProps) {
  const [columnVisibility, setColumnVisibility] = useState<ColumnVisibilityState>({});
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 10 });
  const rowSelection = useMemo<RowSelectionState>(() => Object.fromEntries(selected.map((id) => [id, true])), [selected]);

  // Auto-refresh replaces `runs` every few seconds; only a change in what is shown goes back to page 1.
  const shown = runs.map((run) => run.run_id).join();
  useEffect(() => setPagination((current) => ({ ...current, pageIndex: 0 })), [shown]);

  const table = useTable({
    features,
    data: runs,
    columns,
    state: { columnVisibility, rowSelection, pagination },
    getRowId: (run) => run.run_id,
    enableRowSelection: (row) => row.original.kind === "run",
    autoResetPageIndex: false,
    onRowSelectionChange: (updater: Updater<RowSelectionState>) => {
      const next = typeof updater === "function" ? updater(rowSelection) : updater;
      onSelectedChange(Object.keys(next).filter((id) => next[id]));
    },
    onColumnVisibilityChange: setColumnVisibility,
    onPaginationChange: setPagination,
  });

  const pageCount = Math.max(1, table.getPageCount());
  const visibleColumns = table.getVisibleLeafColumns().length;

  return (
    <Tabs value={view} onValueChange={(value) => onViewChange(value as View)} className="w-full flex-col justify-start gap-4">
      <div className="flex items-center justify-between gap-2">
        <Label htmlFor="view-selector" className="sr-only">
          View
        </Label>
        <Select value={view} onValueChange={(value) => onViewChange(value as View)}>
          <SelectTrigger className="flex w-fit @4xl/main:hidden" size="sm" id="view-selector">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {VIEWS.map(({ value, label }) => (
              <SelectItem key={value} value={value}>
                {label} ({counts[value]})
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <TabsList className="hidden **:data-[slot=badge]:size-5 **:data-[slot=badge]:rounded-full **:data-[slot=badge]:bg-muted-foreground/30 **:data-[slot=badge]:px-1 @4xl/main:flex">
          {VIEWS.map(({ value, label }) => (
            <TabsTrigger key={value} value={value}>
              {label} <Badge variant="secondary">{counts[value]}</Badge>
            </TabsTrigger>
          ))}
        </TabsList>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="sm">
              <Columns2 aria-hidden />
              <span className="hidden lg:inline">Customize columns</span>
              <span className="lg:hidden">Columns</span>
              <ChevronDown aria-hidden />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            {table
              .getAllColumns()
              .filter((col) => col.getCanHide())
              .map((col) => (
                <DropdownMenuCheckboxItem
                  key={col.id}
                  checked={col.getIsVisible()}
                  onCheckedChange={(value) => col.toggleVisibility(!!value)}
                  onSelect={(event) => event.preventDefault()}
                >
                  {HIDEABLE[col.id] ?? col.id}
                </DropdownMenuCheckboxItem>
              ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      <TabsContent value={view} className="flex flex-col gap-4">
      {toolbar}

      <div className="overflow-hidden rounded-lg border">
        <Table>
          <TableHeader className="sticky top-0 z-10 bg-muted">
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id}>
                {group.headers.map((header) => (
                  <TableHead key={header.id} colSpan={header.colSpan} className={CELL_CLASS[header.column.id]}>
                    {header.isPlaceholder ? null : <FlexRender header={header} />}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id} data-state={row.getIsSelected() ? "selected" : undefined}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className={CELL_CLASS[cell.column.id]}>
                      <FlexRender cell={cell} />
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : (
              <TableRow>
                <TableCell colSpan={visibleColumns} className="h-24 text-center">
                  {empty}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <div className="flex items-center justify-between px-2">
        <div className="hidden flex-1 text-sm text-muted-foreground lg:flex">{selected.length} of 2 runs selected for Compare.</div>
        <div className="flex w-full items-center gap-8 lg:w-fit">
          <div className="hidden items-center gap-2 lg:flex">
            <Label htmlFor="rows-per-page" className="text-sm font-medium">
              Rows per page
            </Label>
            <Select value={`${pagination.pageSize}`} onValueChange={(value) => table.setPageSize(Number(value))}>
              <SelectTrigger size="sm" className="w-20" id="rows-per-page">
                <SelectValue />
              </SelectTrigger>
              <SelectContent side="top">
                {[10, 20, 30, 50].map((size) => (
                  <SelectItem key={size} value={`${size}`}>
                    {size}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="flex w-fit items-center justify-center text-sm font-medium" aria-live="polite">
            Page {Math.min(pagination.pageIndex + 1, pageCount)} of {pageCount}
          </div>
          <div className="ml-auto flex items-center gap-2 lg:ml-0">
            <PageButton label="Go to first page" className="hidden lg:flex" onClick={() => table.setPageIndex(0)} disabled={!table.getCanPreviousPage()}>
              <ChevronsLeft />
            </PageButton>
            <PageButton label="Go to previous page" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>
              <ChevronLeft />
            </PageButton>
            <PageButton label="Go to next page" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>
              <ChevronRight />
            </PageButton>
            <PageButton label="Go to last page" className="hidden lg:flex" onClick={() => table.setPageIndex(pageCount - 1)} disabled={!table.getCanNextPage()}>
              <ChevronsRight />
            </PageButton>
          </div>
        </div>
      </div>
      </TabsContent>
    </Tabs>
  );
}

function PageButton({ label, className, children, ...props }: { label: string; className?: string; children: ReactNode; onClick: () => void; disabled: boolean }) {
  return (
    <Button variant="outline" size="icon" className={cn("size-8", className)} {...props}>
      <span className="sr-only">{label}</span>
      {children}
    </Button>
  );
}
