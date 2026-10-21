import { useState } from "react";
import { Play, Plus, Trash2, TriangleAlert } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router";
import { api, ApiError } from "@/api/client";
import type { LibraryEntry } from "@/api/types";
import { FileView } from "@/components/file-view";
import { InspectSummary } from "@/components/inspect-summary";
import { Page } from "@/components/page";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatRelative } from "@/lib/format";
import { useApi } from "@/lib/use-api";

export const runLink = (name: string) => `/new?scenario=${encodeURIComponent(name)}`;

function NewTestButton() {
  return (
    <Button asChild>
      <Link to="/new">
        <Plus aria-hidden />
        New test
      </Link>
    </Button>
  );
}

/** Confirm dialog around DELETE /api/library/:name; closes on success and calls `onDeleted`. */
function DeleteDialog({ name, onClose, onDeleted }: { name: string | null; onClose: () => void; onDeleted: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();

  async function remove() {
    if (!name) return;
    setBusy(true);
    setError(undefined);
    try {
      await api.libraryDelete(name);
      onClose();
      onDeleted();
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error(String(caught)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Dialog open={name !== null} onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Delete {name}?</DialogTitle>
          <DialogDescription>
            This removes the saved test from your library. Runs that already used it stay in the runs folder.
          </DialogDescription>
        </DialogHeader>
        {error && (
          <Alert variant="destructive">
            <TriangleAlert aria-hidden />
            <AlertTitle>Could not delete</AlertTitle>
            <AlertDescription>{error.message}</AlertDescription>
          </Alert>
        )}
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={remove} disabled={busy}>
            {busy ? "Deleting" : "Delete"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function LibraryPage() {
  const { data, error, reload } = useApi("library", (signal) => api.libraryList(signal));
  const [deleting, setDeleting] = useState<string | null>(null);

  return (
    <Page
      title="Library"
      crumbs={[{ label: "Library" }]}
      description="Tests saved on this machine. Run one against your app, or review exactly what it does."
      actions={<NewTestButton />}
    >
      {error && !data ? (
        <ErrorState error={error} onRetry={reload} />
      ) : !data ? (
        <LoadingState label="Loading library" />
      ) : !data.length ? (
        <EmptyState title="Your library is empty" action={<NewTestButton />}>
          Create a test by describing your app, from a tenant-isolation kit, or by pasting scenario files.
        </EmptyState>
      ) : (
        <div className="window-card">
          <div className="window-card-header">library · {data.length} saved</div>
          <Table aria-label="Saved tests">
            <TableHeader className="font-mono text-xs">
              <TableRow>
                <TableHead>Test</TableHead>
                <TableHead className="text-right">Journeys</TableHead>
                <TableHead className="text-right">Checks</TableHead>
                <TableHead>Needs secrets</TableHead>
                <TableHead>Setup</TableHead>
                <TableHead>Modified</TableHead>
                <TableHead>
                  <span className="sr-only">Actions</span>
                </TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((entry) => (
                <LibraryRow key={entry.name} entry={entry} onDelete={() => setDeleting(entry.name)} />
              ))}
            </TableBody>
          </Table>
        </div>
      )}
      <DeleteDialog name={deleting} onClose={() => setDeleting(null)} onDeleted={reload} />
    </Page>
  );
}

function LibraryRow({ entry, onDelete }: { entry: LibraryEntry; onDelete: () => void }) {
  return (
    <TableRow>
      <TableCell className="font-medium">
        <Link to={`/library/${encodeURIComponent(entry.name)}`} className="hover:underline focus-visible:underline">
          {entry.name}
        </Link>
        {entry.error && (
          <p className="mt-1 flex items-start gap-1 text-xs font-normal text-destructive">
            <TriangleAlert className="mt-0.5 size-3 shrink-0" aria-hidden />
            <span className="wrap-anywhere">Invalid: {entry.error}</span>
          </p>
        )}
      </TableCell>
      <TableCell className="text-right tabular-nums">{entry.planned_journeys ?? "n/a"}</TableCell>
      <TableCell className="text-right tabular-nums">{entry.assertions.length}</TableCell>
      <TableCell>
        {entry.secret_env.length ? (
          <span className="flex flex-wrap gap-1">
            {entry.secret_env.map((name) => (
              <Badge key={name} variant="outline" className="font-mono">
                {name}
              </Badge>
            ))}
          </span>
        ) : (
          <span className="text-muted-foreground">None</span>
        )}
      </TableCell>
      <TableCell>{entry.has_command ? <Badge variant="secondary">Setup command</Badge> : <span className="text-muted-foreground">None</span>}</TableCell>
      <TableCell className="text-muted-foreground">{formatRelative(entry.modified_at)}</TableCell>
      <TableCell>
        <span className="flex justify-end gap-2">
          {entry.error ? (
            <Button size="sm" disabled aria-label={`Run ${entry.name}`}>
              <Play aria-hidden />
              Run
            </Button>
          ) : (
            <Button asChild size="sm">
              <Link to={runLink(entry.name)} aria-label={`Run ${entry.name}`}>
                <Play aria-hidden />
                Run
              </Link>
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={onDelete} aria-label={`Delete ${entry.name}`}>
            <Trash2 aria-hidden />
            Delete
          </Button>
        </span>
      </TableCell>
    </TableRow>
  );
}

export function LibraryDetailPage() {
  const name = useParams().name ?? "";
  const navigate = useNavigate();
  const { data, error, reload } = useApi(`library:${name}`, (signal) => api.library(name, signal));
  const [deleting, setDeleting] = useState(false);
  const crumbs = [{ label: "Library", to: "/library" }, { label: name }];

  if (!data) {
    return (
      <Page title={<span className="font-mono text-xl">{name}</span>} documentTitle={name} crumbs={crumbs}>
        {error instanceof ApiError && error.status === 404 ? (
          <EmptyState
            title="Test not found"
            action={
              <Button asChild variant="outline">
                <Link to="/library">Back to library</Link>
              </Button>
            }
          >
            No saved test is named {name}.
          </EmptyState>
        ) : error ? (
          <ErrorState error={error} onRetry={reload} />
        ) : (
          <LoadingState label="Loading test" />
        )}
      </Page>
    );
  }

  return (
    <Page
      title={<span className="font-mono text-xl">{name}</span>}
      documentTitle={name}
      crumbs={crumbs}
      description={<span className="font-mono text-xs">sha256 {data.digest}</span>}
      actions={
        <>
          {data.error ? (
            <Button disabled>
              <Play aria-hidden />
              Run
            </Button>
          ) : (
            <Button asChild>
              <Link to={runLink(name)}>
                <Play aria-hidden />
                Run
              </Link>
            </Button>
          )}
          <Button variant="outline" onClick={() => setDeleting(true)}>
            <Trash2 aria-hidden />
            Delete
          </Button>
        </>
      }
    >
      {data.error && (
        <Alert variant="destructive">
          <TriangleAlert aria-hidden />
          <AlertTitle>This saved test no longer validates</AlertTitle>
          <AlertDescription>
            <pre className="font-mono text-xs whitespace-pre-wrap">{data.error}</pre>
          </AlertDescription>
        </Alert>
      )}
      {data.inspect && (
        <Card>
          <CardHeader>
            <CardTitle>What this test does</CardTitle>
            <CardDescription>From the scenario as saved, without running anything.</CardDescription>
          </CardHeader>
          <CardContent className="grid gap-4">
            <InspectSummary inspect={data.inspect} />
            {data.inspect.secret_env.length > 0 && (
              <p className="text-sm text-muted-foreground">
                Needs secrets: <span className="font-mono">{data.inspect.secret_env.join(", ")}</span>. You enter them when you run it.
              </p>
            )}
          </CardContent>
        </Card>
      )}
      <section aria-label="Files" className="grid gap-3">
        <h2 className="text-lg font-semibold">Files</h2>
        <p className="text-sm text-muted-foreground">Scenarios are code that runs on your machine. Read them before you run.</p>
        {Object.entries(data.files).map(([path, content]) => (
          <FileView key={path} path={path} content={content} />
        ))}
      </section>
      <DeleteDialog name={deleting ? name : null} onClose={() => setDeleting(false)} onDeleted={() => navigate("/library")} />
    </Page>
  );
}
