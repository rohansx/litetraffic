import { useState } from "react";
import { ChevronDown, Play, TriangleAlert } from "lucide-react";
import { Link, useNavigate } from "react-router";
import { api, ApiError } from "@/api/client";
import type { LibraryEntry, Meta } from "@/api/types";
import { Facts } from "@/components/facts";
import { InspectSummary } from "@/components/inspect-summary";
import { EmptyState, ErrorState, LoadingState } from "@/components/states";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useApi } from "@/lib/use-api";
import { CreateTabs, ErrorAlert, Field } from "@/pages/new/create";

/** Everything step 2 collects. Secret values live only here and in the run request: never in the URL, storage or any later screen. */
export interface RunSettings {
  target: string;
  seed: string;
  repeat: string;
  capture: string;
  env: Record<string, string>;
  confirmed: boolean;
}

export const INITIAL_SETTINGS: RunSettings = { target: "", seed: "0", repeat: "1", capture: "", env: {}, confirmed: false };

const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

/** The target as a URL when it looks like http(s)://host, else null. */
export function parseTarget(text: string): URL | null {
  if (!/^https?:\/\/[^\s/]+/i.test(text.trim())) return null;
  try {
    return new URL(text.trim());
  } catch {
    return null;
  }
}

export function isLocalHost(url: URL): boolean {
  return LOCAL_HOSTS.has(url.hostname.toLowerCase());
}

function integer(text: string): number | null {
  return /^-?\d+$/.test(text.trim()) ? Number(text) : null;
}

export function settingsProblems(settings: RunSettings): { target?: string; seed?: string; repeat?: string } {
  const problems: { target?: string; seed?: string; repeat?: string } = {};
  if (!parseTarget(settings.target)) problems.target = "Enter an address that starts with http:// or https://.";
  const seed = integer(settings.seed);
  if (seed === null || seed < 0) problems.seed = "The seed is a whole number, 0 or more.";
  const repeat = integer(settings.repeat);
  if (repeat === null || repeat < 1 || repeat > 20) problems.repeat = "Repeat between 1 and 20 times.";
  return problems;
}

export function ChooseStep({ meta, onChosen }: { meta?: Meta; onChosen: (name: string) => void }) {
  const { data, error, reload } = useApi("library", (signal) => api.libraryList(signal));
  return (
    <div className="grid gap-6">
      <section aria-label="Saved tests" className="grid gap-3">
        <h2 className="text-lg font-semibold">Pick a saved test</h2>
        {error && !data ? (
          <ErrorState error={error} onRetry={reload} />
        ) : !data ? (
          <LoadingState label="Loading library" />
        ) : !data.length ? (
          <EmptyState title="No saved tests yet">Create your first one below.</EmptyState>
        ) : (
          <ul className="grid gap-3 md:grid-cols-2 @5xl/main:grid-cols-3">
            {data.map((entry) => (
              <li key={entry.name}>
                <TestCard entry={entry} onChoose={() => onChosen(entry.name)} />
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-label="Create a test" className="grid gap-3">
        <h2 className="text-lg font-semibold">Or create one</h2>
        <CreateTabs meta={meta} onChosen={onChosen} />
      </section>
    </div>
  );
}

function TestCard({ entry, onChoose }: { entry: LibraryEntry; onChoose: () => void }) {
  return (
    <Card className="h-full">
      <CardHeader>
        <CardTitle className="font-mono text-base wrap-anywhere">{entry.name}</CardTitle>
        <CardDescription>
          {entry.planned_journeys ?? "n/a"} journeys · {entry.assertions.length} checks
        </CardDescription>
      </CardHeader>
      <CardContent className="mt-auto grid gap-3">
        {entry.error ? (
          <p className="text-xs text-destructive wrap-anywhere">Invalid: {entry.error}</p>
        ) : entry.secret_env.length ? (
          <p className="text-xs text-muted-foreground">
            Needs secrets: <span className="font-mono">{entry.secret_env.join(", ")}</span>
          </p>
        ) : (
          <p className="text-xs text-muted-foreground">Needs no secrets.</p>
        )}
        {entry.has_command && <Badge variant="secondary">Setup command</Badge>}
        <Button onClick={onChoose} disabled={Boolean(entry.error)} aria-label={`Use ${entry.name}`} className="w-fit">
          Use this test
        </Button>
      </CardContent>
    </Card>
  );
}

export function AimStep({
  scenario,
  settings,
  onChange,
  onBack,
  onNext,
}: {
  scenario: string;
  settings: RunSettings;
  onChange: (next: RunSettings) => void;
  onBack: () => void;
  onNext: () => void;
}) {
  const { data, error, reload } = useApi(`library:${scenario}`, (signal) => api.library(scenario, signal));
  const problems = settingsProblems(settings);
  const url = parseTarget(settings.target);
  const secretNames = data?.inspect?.secret_env ?? [];
  const canContinue = Object.keys(problems).length === 0 && settings.confirmed;
  const set = (patch: Partial<RunSettings>) => onChange({ ...settings, ...patch });

  if (!data) {
    return error instanceof ApiError && error.status === 404 ? (
      <EmptyState
        title="Test not found"
        action={<Button variant="outline" onClick={onBack}>Choose another test</Button>}
      >
        No saved test is named {scenario}.
      </EmptyState>
    ) : error ? (
      <ErrorState error={error} onRetry={reload} />
    ) : (
      <LoadingState label="Loading test" />
    );
  }

  return (
    <div className="grid max-w-3xl gap-6">
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
        <Collapsible defaultOpen>
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center justify-between gap-2">
                <CollapsibleTrigger className="group flex items-center gap-2 rounded-md text-left focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none">
                  What this test will do
                  <ChevronDown className="size-4 transition-transform group-data-[state=open]:rotate-180" aria-hidden />
                </CollapsibleTrigger>
              </CardTitle>
              <CardDescription>
                <span className="font-mono">{scenario}</span>, as saved. Nothing has run yet.
              </CardDescription>
            </CardHeader>
            <CollapsibleContent>
              <CardContent>
                <InspectSummary inspect={data.inspect} />
              </CardContent>
            </CollapsibleContent>
          </Card>
        </Collapsible>
      )}

      <form
        className="grid gap-4"
        aria-label="Aim the test"
        onSubmit={(event) => {
          event.preventDefault();
          if (canContinue) onNext();
        }}
      >
        <Field
          label="Target URL"
          htmlFor="run-target"
          hint={settings.target && problems.target ? <span className="text-destructive">{problems.target}</span> : "Where your app is listening, for example http://127.0.0.1:8000."}
        >
          <Input
            id="run-target"
            type="url"
            inputMode="url"
            value={settings.target}
            onChange={(event) => set({ target: event.target.value })}
            aria-invalid={Boolean(settings.target && problems.target)}
            placeholder="http://127.0.0.1:8000"
            autoComplete="off"
            spellCheck={false}
          />
        </Field>
        {url && !isLocalHost(url) && (
          <Alert className="border-inconclusive bg-inconclusive-muted text-inconclusive">
            <TriangleAlert aria-hidden />
            <AlertTitle>This is not a local address</AlertTitle>
            <AlertDescription className="text-inconclusive">
              {url.host} is not localhost. Prefer a staging copy: the test sends real requests, including writes, and creates test data.
            </AlertDescription>
          </Alert>
        )}
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Seed" htmlFor="run-seed" hint={problems.seed ? <span className="text-destructive">{problems.seed}</span> : "The same seed replays the same schedule."}>
            <Input id="run-seed" type="number" inputMode="numeric" min={0} step={1} value={settings.seed} onChange={(event) => set({ seed: event.target.value })} aria-invalid={Boolean(problems.seed)} />
          </Field>
          <Field label="Repeat" htmlFor="run-repeat" hint={problems.repeat ? <span className="text-destructive">{problems.repeat}</span> : "Run it 1 to 20 times in a row."}>
            <Input id="run-repeat" type="number" inputMode="numeric" min={1} max={20} step={1} value={settings.repeat} onChange={(event) => set({ repeat: event.target.value })} aria-invalid={Boolean(problems.repeat)} />
          </Field>
        </div>
        <Field label="Docker capture (optional)" htmlFor="run-capture" hint="A container to capture server-side evidence from, for example a container name. Leave blank to skip.">
          <Input id="run-capture" value={settings.capture} onChange={(event) => set({ capture: event.target.value })} autoComplete="off" spellCheck={false} className="font-mono" />
        </Field>

        {secretNames.length > 0 && (
          <fieldset className="grid gap-4 rounded-lg border p-4">
            <legend className="px-1 text-sm font-medium">Secrets this test needs</legend>
            <p className="text-xs text-muted-foreground">
              Sent with this run only, kept in memory by the console and never shown again. Leave one blank if it is already set where the console runs.
            </p>
            {secretNames.map((name) => (
              <Field key={name} label={name} htmlFor={`secret-${name}`}>
                <Input
                  id={`secret-${name}`}
                  type="password"
                  autoComplete="off"
                  value={settings.env[name] ?? ""}
                  onChange={(event) => set({ env: { ...settings.env, [name]: event.target.value } })}
                  className="font-mono"
                />
              </Field>
            ))}
          </fieldset>
        )}

        <div className="flex items-start gap-2 rounded-lg border p-4">
          <Checkbox id="run-confirm" checked={settings.confirmed} onCheckedChange={(value) => set({ confirmed: value === true })} className="mt-0.5" />
          <Label htmlFor="run-confirm" className="items-start leading-snug font-normal">
            I own this app or have permission to test it. This sends real requests, including writes, to the address above.
          </Label>
        </div>

        <div className="flex flex-wrap gap-2">
          <Button type="button" variant="outline" onClick={onBack}>
            Back
          </Button>
          <Button type="submit" disabled={!canContinue}>
            Continue
          </Button>
        </div>
      </form>
    </div>
  );
}

export function RunStep({ scenario, settings, onBack }: { scenario: string; settings: RunSettings; onBack: () => void }) {
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error>();
  const names = Object.keys(settings.env).filter((name) => settings.env[name] !== "");

  async function run() {
    setBusy(true);
    setError(undefined);
    try {
      const env = Object.fromEntries(names.map((name) => [name, settings.env[name]!]));
      const started = await api.runJob({
        scenario,
        target: settings.target.trim(),
        seed: Number(settings.seed),
        repeat: Number(settings.repeat),
        ...(names.length ? { env } : {}),
        ...(settings.capture.trim() ? { capture: settings.capture.trim() } : {}),
        confirm_target: true,
      });
      navigate(`/jobs/${encodeURIComponent(started.id)}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught : new Error(String(caught)));
      setBusy(false);
    }
  }

  return (
    <div className="grid max-w-3xl gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Ready to run</CardTitle>
          <CardDescription>Check the details, then start. You can watch it live and cancel at any time.</CardDescription>
        </CardHeader>
        <CardContent>
          <Facts
            className="sm:grid-cols-2 lg:grid-cols-2"
            items={[
              ["Test", <span key="s" className="font-mono">{scenario}</span>],
              ["Target", <span key="t" className="font-mono">{settings.target.trim()}</span>],
              ["Seed", settings.seed],
              ["Repeat", settings.repeat],
              ["Docker capture", settings.capture.trim() || "None"],
              ["Secrets provided", names.length ? <span key="e" className="font-mono">{names.join(", ")}</span> : "None"],
            ]}
          />
        </CardContent>
      </Card>
      {error && <ErrorAlert title="Could not start the run" error={error} />}
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" onClick={onBack} disabled={busy}>
          Back
        </Button>
        <Button onClick={run} disabled={busy || !settings.confirmed}>
          <Play aria-hidden />
          {busy ? "Starting" : "Run test"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        By running you confirm you own this app or have permission to test it. <Link to="/library" className="underline">Back to library</Link>
      </p>
    </div>
  );
}
