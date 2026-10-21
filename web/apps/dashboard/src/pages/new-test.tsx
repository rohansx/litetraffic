import { useState } from "react";
import { Check } from "lucide-react";
import { useSearchParams } from "react-router";
import { api } from "@/api/client";
import { Page } from "@/components/page";
import { cn } from "@/lib/utils";
import { useApi } from "@/lib/use-api";
import { AimStep, ChooseStep, INITIAL_SETTINGS, RunStep, type RunSettings } from "@/pages/new/steps";

const STEPS = ["Choose a test", "Aim it", "Run"];

function Stepper({ step, onGo }: { step: number; onGo: (step: number) => void }) {
  return (
    <ol aria-label="Steps" className="flex flex-wrap gap-x-6 gap-y-2">
      {STEPS.map((label, index) => {
        const number = index + 1;
        const done = number < step;
        const current = number === step;
        const body = (
          <>
            <span
              aria-hidden
              className={cn(
                "flex size-6 shrink-0 items-center justify-center rounded-full border font-mono text-xs",
                current && "border-primary bg-primary text-primary-foreground",
                done && "border-pass bg-pass-muted text-pass",
              )}
            >
              {done ? <Check className="size-3.5" /> : number}
            </span>
            <span className={cn("text-sm", current ? "font-semibold" : "text-muted-foreground")}>
              <span className="sr-only">Step {number}: </span>
              {label}
            </span>
          </>
        );
        return (
          <li key={label} aria-current={current ? "step" : undefined}>
            {done ? (
              <button type="button" onClick={() => onGo(number)} className="flex items-center gap-2 rounded-md focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none">
                {body}
              </button>
            ) : (
              <span className="flex items-center gap-2">{body}</span>
            )}
          </li>
        );
      })}
    </ol>
  );
}

export function NewTestPage() {
  const [params, setParams] = useSearchParams();
  const preset = params.get("scenario");
  const [scenario, setScenario] = useState(preset ?? "");
  const [step, setStep] = useState(preset ? 2 : 1);
  const [settings, setSettings] = useState<RunSettings>(INITIAL_SETTINGS);
  const meta = useApi("meta", (signal) => api.meta(signal));

  function choose(name: string) {
    setScenario(name);
    setSettings(INITIAL_SETTINGS);
    setStep(2);
    setParams({ scenario: name }, { replace: true });
  }
  function goTo(next: number) {
    if (next === 1) setParams({}, { replace: true });
    setStep(next);
  }

  return (
    <Page
      title="New test"
      crumbs={[{ label: "New test" }]}
      description="Choose a test, aim it at your app, and watch it run. Nothing is sent until the last step."
    >
      <Stepper step={step} onGo={goTo} />
      {step === 1 && <ChooseStep meta={meta.data} onChosen={choose} />}
      {step === 2 && scenario && <AimStep scenario={scenario} settings={settings} onChange={setSettings} onBack={() => goTo(1)} onNext={() => goTo(3)} />}
      {step === 3 && scenario && <RunStep scenario={scenario} settings={settings} onBack={() => goTo(2)} />}
    </Page>
  );
}
