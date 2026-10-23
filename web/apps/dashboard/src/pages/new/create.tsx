import { useState, type FormEvent, type ReactNode } from "react";
import { Plus, Sparkles, Trash2, TriangleAlert } from "lucide-react";
import { Link } from "react-router";
import { api, ApiError } from "@/api/client";
import type { Meta } from "@/api/types";
import { DraftResultView } from "@/components/draft-result";
import { JobLog } from "@/components/job-log";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { useJob } from "@/lib/use-job";

export const NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{0,62}$/;
const NAME_HINT = "Lowercase letters, digits, hyphen and underscore; up to 63 characters.";

const KIT_EXAMPLE = {
  name: "my-api-tenant-isolation",
  identities: [
    { name: "tenant-a", token_env: "TENANT_A_TOKEN", resources: ["org-a-1"], markers: ["marker-a-private"] },
    { name: "tenant-b", token_env: "TENANT_B_TOKEN", resources: ["org-b-1"], markers: ["marker-b-private"] },
  ],
  endpoints: [
    { method: "GET", path: "/api/orgs/{id}/items", kind: "read" },
    { method: "POST", path: "/api/orgs/{id}/items", kind: "write", body: { title: "probe" } },
  ],
};

export function Field({ label, hint, htmlFor, children }: { label: string; hint?: ReactNode; htmlFor: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={htmlFor}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  );
}

export function ErrorAlert({ title, error }: { title: string; error: Error }) {
  const busyJob = error instanceof ApiError && error.status === 409 ? error.body?.job_id : undefined;
  return (
    <Alert variant="destructive">
      <TriangleAlert aria-hidden />
      <AlertTitle>{busyJob ? "A test is already running" : title}</AlertTitle>
      <AlertDescription>
        <pre className="font-mono text-xs break-words whitespace-pre-wrap">{error.message}</pre>
        {busyJob && (
          <Link to={`/jobs/${encodeURIComponent(busyJob)}`} className="underline">
            Open the running job
          </Link>
        )}
      </AlertDescription>
    </Alert>
  );
}

const asError = (caught: unknown) => (caught instanceof Error ? caught : new Error(String(caught)));

/**
 * Saves files to the library. Handles the three answers the server can give beyond success:
 * 400 needs_trust (show the setup command, ask for trust), 409 exists (offer overwrite), 422 invalid (show why).
 */
export function SaveBar({
  name,
  files,
  disabled,
  onSaved,
}: {
  name: string;
  files: Record<string, string>;
  disabled?: boolean;
  onSaved: (name: string) => void;
}) {
  const [trust, setTrust] = useState(false);
  const [command, setCommand] = useState<string[] | null>(null);
  const [exists, setExists] = useState(false);
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);

  async function save(overwrite = false) {
    setBusy(true);
    setError(undefined);
    try {
      const saved = await api.librarySave({
        name,
        files,
        ...(trust ? { trust_command: true } : {}),
        ...(overwrite ? { overwrite: true } : {}),
      });
      onSaved(saved.name);
    } catch (caught) {
      if (caught instanceof ApiError && caught.status === 400 && caught.body?.needs_trust) {
        setCommand(caught.body.command ?? []);
        setTrust(false);
        setExists(false);
      } else if (caught instanceof ApiError && caught.status === 409) {
        setExists(true);
        setError(caught);
      } else {
        setError(asError(caught));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid gap-3">
      {command && (
        <Alert className="border-inconclusive bg-inconclusive-muted text-inconclusive">
          <TriangleAlert aria-hidden />
          <AlertTitle>This test runs a setup command on your machine</AlertTitle>
          <AlertDescription className="text-inconclusive">
            <p>Before every run it starts the command below, outside any sandbox. Only trust it if you wrote it or have read it.</p>
            <code className="block rounded-md bg-background/60 p-2 font-mono text-xs break-all text-foreground">{command.join(" ")}</code>
            <div className="mt-1 flex items-center gap-2">
              <Checkbox id="trust-command" checked={trust} onCheckedChange={(value) => setTrust(value === true)} />
              <Label htmlFor="trust-command">Trust setup commands</Label>
            </div>
          </AlertDescription>
        </Alert>
      )}
      {error && <ErrorAlert title="Could not save" error={error} />}
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => save(false)} disabled={busy || disabled || (command !== null && !trust)}>
          {busy ? "Saving" : "Save to library"}
        </Button>
        {exists && (
          <Button variant="outline" onClick={() => save(true)} disabled={busy || (command !== null && !trust)}>
            Overwrite existing test
          </Button>
        )}
      </div>
    </div>
  );
}

function NameField({ id, value, onChange }: { id: string; value: string; onChange: (value: string) => void }) {
  const invalid = value !== "" && !NAME_PATTERN.test(value);
  return (
    <Field label="Test name" htmlFor={id} hint={NAME_HINT}>
      <Input
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        aria-invalid={invalid}
        autoComplete="off"
        spellCheck={false}
        placeholder="my-api-tenant-isolation"
        className="font-mono"
      />
    </Field>
  );
}

export function CreateTabs({ meta, onChosen }: { meta?: Meta; onChosen: (name: string) => void }) {
  return (
    <Tabs defaultValue="describe">
      <TabsList className="h-auto w-full flex-wrap sm:w-fit">
        <TabsTrigger value="describe">Describe it (AI)</TabsTrigger>
        <TabsTrigger value="kit">Tenant isolation kit</TabsTrigger>
        <TabsTrigger value="paste">Paste files</TabsTrigger>
      </TabsList>
      <TabsContent value="describe" className="pt-4">
        <DescribeForm meta={meta} onChosen={onChosen} />
      </TabsContent>
      <TabsContent value="kit" className="pt-4">
        <KitForm onChosen={onChosen} />
      </TabsContent>
      <TabsContent value="paste" className="pt-4">
        <PasteForm onChosen={onChosen} />
      </TabsContent>
    </Tabs>
  );
}

function DescribeForm({ meta, onChosen }: { meta?: Meta; onChosen: (name: string) => void }) {
  const [name, setName] = useState("");
  const [app, setApp] = useState("");
  const [rules, setRules] = useState("");
  const [specText, setSpecText] = useState("");
  const [specUrl, setSpecUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  const needsKey = meta ? !meta.console.ai.available : false;
  const ready = NAME_PATTERN.test(name) && app.trim() !== "" && rules.trim() !== "" && (!needsKey || apiKey.trim() !== "");

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(undefined);
    try {
      const started = await api.draftStart({
        name,
        app_description: app.trim(),
        rules: rules.trim(),
        ...(specText.trim() ? { spec_text: specText } : {}),
        ...(specUrl.trim() ? { spec_url: specUrl.trim() } : {}),
        ...(needsKey && apiKey.trim() ? { api_key: apiKey.trim() } : {}),
      });
      setJobId(started.id);
    } catch (caught) {
      setError(asError(caught));
    } finally {
      setBusy(false);
    }
  }

  if (jobId) return <DraftRunner id={jobId} onRestart={() => setJobId(null)} onSaved={onChosen} />;

  return (
    <form onSubmit={submit} className="grid max-w-3xl gap-4" aria-label="Describe your test">
      <NameField id="draft-name" value={name} onChange={setName} />
      <Field label="What the app is" htmlFor="draft-app" hint="One or two sentences: what it does, who uses it, what data it holds.">
        <Textarea id="draft-app" value={app} onChange={(event) => setApp(event.target.value)} rows={3} />
      </Field>
      <Field label="Rules that must never break" htmlFor="draft-rules" hint="In plain words, one per line. For example: a customer can never read another customer's invoices.">
        <Textarea id="draft-rules" value={rules} onChange={(event) => setRules(event.target.value)} rows={4} />
      </Field>
      <div className="grid gap-4 md:grid-cols-2">
        <Field label="API spec (paste)" htmlFor="draft-spec-text" hint="Optional. OpenAPI or any description of the endpoints.">
          <Textarea id="draft-spec-text" value={specText} onChange={(event) => setSpecText(event.target.value)} rows={4} className="font-mono text-xs" />
        </Field>
        <Field label="API spec URL" htmlFor="draft-spec-url" hint="Optional. Use this or paste a spec, not both required.">
          <Input id="draft-spec-url" type="url" inputMode="url" value={specUrl} onChange={(event) => setSpecUrl(event.target.value)} placeholder="https://example.com/openapi.json" />
        </Field>
      </div>
      {needsKey && (
        <Field
          label="Anthropic API key"
          htmlFor="draft-api-key"
          hint="No key is configured on this machine. This one is used once for this draft and kept in memory only: it is never written to disk."
        >
          <Input id="draft-api-key" type="password" autoComplete="off" value={apiKey} onChange={(event) => setApiKey(event.target.value)} />
        </Field>
      )}
      {error && <ErrorAlert title="Could not start the draft" error={error} />}
      <div>
        <Button type="submit" disabled={!ready || busy}>
          <Sparkles aria-hidden />
          {busy ? "Starting" : "Draft with AI"}
        </Button>
      </div>
    </form>
  );
}

/** Follows a draft job: a live feed while it works, then the files to review and save. */
function DraftRunner({ id, onRestart, onSaved }: { id: string; onRestart: () => void; onSaved: (name: string) => void }) {
  const { job, error } = useJob(id);
  if (error && !job) return <ErrorAlert title="Could not follow the draft" error={error} />;
  if (!job) return <p role="status">Starting the draft</p>;
  const running = job.state === "running";
  const result = job.result;

  return (
    <div className="grid max-w-3xl gap-4">
      <section aria-label="What Claude is doing" className="grid gap-2">
        <h3 className="flex items-center gap-2 text-sm font-medium">
          <Sparkles className="size-4" aria-hidden />
          {running ? "What Claude is doing" : job.state === "cancelled" ? "Draft cancelled" : "What Claude did"}
        </h3>
        <JobLog lines={job.log} label="Draft progress" />
      </section>
      {!running && result && <DraftResultView result={result} />}
      {!running && !result && (
        <Alert variant="destructive">
          <TriangleAlert aria-hidden />
          <AlertTitle>The draft did not produce a result</AlertTitle>
          <AlertDescription>Read the log above for the reason.</AlertDescription>
        </Alert>
      )}
      {!running && (
        <div className="grid gap-3">
          {result?.valid && <SaveBar name={result.name} files={result.files} onSaved={onSaved} />}
          <div>
            <Button variant="outline" onClick={onRestart}>
              {result?.valid ? "Discard and start over" : "Edit and try again"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

function KitForm({ onChosen }: { onChosen: (name: string) => void }) {
  const [name, setName] = useState("");
  const [config, setConfig] = useState("");
  const [error, setError] = useState<Error>();
  const [busy, setBusy] = useState(false);
  let parsed: object | undefined;
  let parseError: string | undefined;
  if (config.trim()) {
    try {
      const value: unknown = JSON.parse(config);
      if (value && typeof value === "object" && !Array.isArray(value)) parsed = value;
      else parseError = "The config must be a JSON object.";
    } catch (caught) {
      parseError = `Not valid JSON: ${asError(caught).message}`;
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!parsed) return;
    setBusy(true);
    setError(undefined);
    try {
      const saved = await api.libraryKit(name, parsed);
      onChosen(saved.name);
    } catch (caught) {
      setError(asError(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form onSubmit={submit} className="grid max-w-3xl gap-4" aria-label="Tenant isolation kit">
      <p className="text-sm text-muted-foreground">
        Two identities and the endpoints to probe: the kit builds a complete cross-tenant read and write test from this config.
      </p>
      <NameField id="kit-name" value={name} onChange={setName} />
      <Field
        label="Kit config (JSON)"
        htmlFor="kit-config"
        hint={parseError ? <span className="text-destructive">{parseError}</span> : "Identities take token_env (a bearer token read from the environment), headers, or JWT auth."}
      >
        <Textarea id="kit-config" value={config} onChange={(event) => setConfig(event.target.value)} rows={12} spellCheck={false} aria-invalid={Boolean(parseError)} className="font-mono text-xs" />
      </Field>
      {error && <ErrorAlert title="Could not build the kit" error={error} />}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            setConfig(JSON.stringify(KIT_EXAMPLE, null, 2));
            setName(KIT_EXAMPLE.name);
          }}
        >
          Load example
        </Button>
        <Button type="submit" disabled={busy || !parsed || !NAME_PATTERN.test(name)}>
          {busy ? "Building" : "Save to library"}
        </Button>
      </div>
    </form>
  );
}

interface PastedFile {
  key: number;
  path: string;
  content: string;
}

function PasteForm({ onChosen }: { onChosen: (name: string) => void }) {
  const [name, setName] = useState("");
  const [files, setFiles] = useState<PastedFile[]>([
    { key: 0, path: "manifest.json", content: "" },
    { key: 1, path: "journeys.js", content: "" },
  ]);
  const [nextKey, setNextKey] = useState(2);
  const paths = files.map((file) => file.path.trim());
  const duplicate = paths.find((path, index) => path && paths.indexOf(path) !== index);
  const ready = NAME_PATTERN.test(name) && files.every((file) => file.path.trim() && file.content.trim()) && !duplicate;
  const update = (key: number, patch: Partial<PastedFile>) =>
    setFiles((current) => current.map((file) => (file.key === key ? { ...file, ...patch } : file)));

  return (
    <div className="grid max-w-3xl gap-4" role="group" aria-label="Paste scenario files">
      <p className="text-sm text-muted-foreground">Paste a scenario bundle: manifest.json plus the files it references.</p>
      <NameField id="paste-name" value={name} onChange={setName} />
      {files.map((file, index) => (
        <fieldset key={file.key} className="grid gap-2 rounded-lg border p-3">
          <legend className="px-1 text-xs text-muted-foreground">File {index + 1}</legend>
          <div className="flex items-end gap-2">
            <div className="grid flex-1 gap-1.5">
              <Label htmlFor={`paste-path-${file.key}`}>File {index + 1} path</Label>
              <Input id={`paste-path-${file.key}`} value={file.path} onChange={(event) => update(file.key, { path: event.target.value })} className="font-mono" spellCheck={false} />
            </div>
            <Button
              type="button"
              variant="outline"
              size="icon"
              aria-label={`Remove file ${index + 1}`}
              disabled={files.length <= 1}
              onClick={() => setFiles((current) => current.filter((item) => item.key !== file.key))}
            >
              <Trash2 aria-hidden />
            </Button>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor={`paste-content-${file.key}`}>File {index + 1} content</Label>
            <Textarea id={`paste-content-${file.key}`} value={file.content} onChange={(event) => update(file.key, { content: event.target.value })} rows={8} spellCheck={false} className="font-mono text-xs" />
          </div>
        </fieldset>
      ))}
      {duplicate && <p className="text-sm text-destructive">Two files are named {duplicate}.</p>}
      <div>
        <Button
          type="button"
          variant="outline"
          onClick={() => {
            setFiles((current) => [...current, { key: nextKey, path: "", content: "" }]);
            setNextKey((value) => value + 1);
          }}
        >
          <Plus aria-hidden />
          Add file
        </Button>
      </div>
      <SaveBar
        name={name}
        files={Object.fromEntries(files.map((file) => [file.path.trim(), file.content]))}
        disabled={!ready}
        onSaved={onChosen}
      />
    </div>
  );
}
