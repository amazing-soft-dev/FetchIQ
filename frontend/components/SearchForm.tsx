"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { createJob, createPreset, getGeo, listPresets, startJob } from "../lib/api";
import { searchCriteriaError } from "../lib/schemas";
import {
  UNITED_STATES,
  emptyCriteria,
  type AccountAge,
  type Preset,
  type SearchCriteria,
} from "../lib/types";
import { ChipMultiSelect } from "./ChipMultiSelect";
import { Button } from "./ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";
import { Field } from "./ui/field";
import { Input } from "./ui/input";

function parseKeywords(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

const AGE_DEFAULTS: AccountAge = emptyCriteria().account_age;
const LIMIT_DEFAULT = emptyCriteria().candidate_limit;

function emptyAgeInputs(): Record<keyof AccountAge, string> {
  return { min_years: "", min_months: "", max_years: "", max_months: "" };
}

function parseAgeField(raw: string, fallback: number): number {
  const text = raw.trim();
  if (text === "") {
    return fallback;
  }
  return Number(text);
}

function accountAgeFromInputs(inputs: Record<keyof AccountAge, string>): AccountAge {
  return {
    min_years: parseAgeField(inputs.min_years, AGE_DEFAULTS.min_years),
    min_months: parseAgeField(inputs.min_months, AGE_DEFAULTS.min_months),
    max_years: parseAgeField(inputs.max_years, AGE_DEFAULTS.max_years),
    max_months: parseAgeField(inputs.max_months, AGE_DEFAULTS.max_months),
  };
}

export function SearchForm() {
  const router = useRouter();
  const [catalog, setCatalog] = useState<{ countries: string[]; us_states: string[] } | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [criteria, setCriteria] = useState<SearchCriteria>(emptyCriteria);
  const [ageInputs, setAgeInputs] = useState(emptyAgeInputs);
  const [limitInput, setLimitInput] = useState("");
  const [keywordText, setKeywordText] = useState("");
  const [presetName, setPresetName] = useState("");
  const [presetVersion, setPresetVersion] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    Promise.all([getGeo(), listPresets()])
      .then(([geo, saved]) => {
        if (!cancelled) {
          setCatalog(geo);
          setPresets(saved);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Could not load search options.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const unitedStatesSelected = criteria.countries.includes(UNITED_STATES);

  function currentCriteria(): SearchCriteria {
    return {
      ...criteria,
      us_states: unitedStatesSelected ? criteria.us_states : [],
      keywords: parseKeywords(keywordText),
      account_age: accountAgeFromInputs(ageInputs),
      candidate_limit: limitInput.trim() === "" ? LIMIT_DEFAULT : Number(limitInput),
    };
  }

  function setAgeInput(field: keyof AccountAge, value: string) {
    setAgeInputs((current) => ({ ...current, [field]: value }));
  }

  function setCountries(countries: string[]) {
    setCriteria((current) => ({
      ...current,
      countries,
      us_states: countries.includes(UNITED_STATES) ? current.us_states : [],
    }));
  }

  function setStates(usStates: string[]) {
    setCriteria((current) => ({ ...current, us_states: usStates }));
  }

  function loadPreset(preset: Preset) {
    const age = preset.criteria.account_age;
    setCriteria(preset.criteria);
    setAgeInputs({
      min_years: String(age.min_years),
      min_months: String(age.min_months),
      max_years: String(age.max_years),
      max_months: String(age.max_months),
    });
    setLimitInput(String(preset.criteria.candidate_limit));
    setKeywordText(preset.criteria.keywords.join(", "));
    setPresetVersion((version) => version + 1);
    setError(null);
  }

  async function savePreset() {
    const next = currentCriteria();
    const problem = searchCriteriaError(next);
    if (problem) {
      setError(problem);
      return;
    }
    if (!presetName.trim()) {
      setError("Name the preset before saving it.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const saved = await createPreset(presetName, next);
      setPresets((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
      setPresetName("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not save the preset.");
    } finally {
      setBusy(false);
    }
  }

  async function startSearch() {
    const next = currentCriteria();
    const problem = searchCriteriaError(next);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const job = await createJob(next);
      await startJob(job.id);
      router.push(`/jobs/${job.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not start the search.");
      setBusy(false);
    }
  }

  return (
    <form
      className="rounded-panel border border-line bg-white p-5 shadow-panel"
      noValidate
      onSubmit={(event) => {
        event.preventDefault();
        void startSearch();
      }}
    >
      <div className="mb-5">
        <h1 className="text-lg font-semibold tracking-tight">Search public GitHub profiles</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Location and account age seed the search. Contact fields are filled only from what a
          developer has published on their profile or in a public README.
        </p>
      </div>

      {error ? <p className="mb-4 text-sm text-danger">{error}</p> : null}

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <ChipMultiSelect
          key={`countries-${presetVersion}`}
          label="Location"
          helper="Select one or more countries or locations"
          placeholder="Search and add locations..."
          options={catalog?.countries ?? []}
          value={criteria.countries}
          onChange={setCountries}
          countNoun="location"
        />

        <fieldset>
          <legend className="text-[13px] font-medium text-muted">Account age</legend>
          <div className="mt-1 grid grid-cols-2 gap-3">
            <Field label="Minimum years">
              <Input
                type="number"
                min={0}
                max={100}
                placeholder={String(AGE_DEFAULTS.min_years)}
                value={ageInputs.min_years}
                onChange={(event) => setAgeInput("min_years", event.target.value)}
              />
            </Field>
            <Field label="Minimum months">
              <Input
                type="number"
                min={0}
                max={11}
                placeholder={String(AGE_DEFAULTS.min_months)}
                value={ageInputs.min_months}
                onChange={(event) => setAgeInput("min_months", event.target.value)}
              />
            </Field>
            <Field label="Maximum years">
              <Input
                type="number"
                min={0}
                max={100}
                placeholder={String(AGE_DEFAULTS.max_years)}
                value={ageInputs.max_years}
                onChange={(event) => setAgeInput("max_years", event.target.value)}
              />
            </Field>
            <Field label="Maximum months">
              <Input
                type="number"
                min={0}
                max={11}
                placeholder={String(AGE_DEFAULTS.max_months)}
                value={ageInputs.max_months}
                onChange={(event) => setAgeInput("max_months", event.target.value)}
              />
            </Field>
          </div>
        </fieldset>
      </div>

      {unitedStatesSelected ? (
        <div className="mt-4">
          <ChipMultiSelect
            key={`states-${presetVersion}`}
            label="US states"
            placeholder="Search and add states..."
            options={catalog?.us_states ?? []}
            value={criteria.us_states}
            onChange={setStates}
            countNoun="state"
          />
        </div>
      ) : null}

      <div className="mt-4 grid gap-4 sm:grid-cols-2">
        <Field label="Keywords">
          <Input
            type="text"
            value={keywordText}
            placeholder="Optional, comma-separated"
            onChange={(event) => setKeywordText(event.target.value)}
          />
        </Field>
        <Field label="Candidate limit">
          <Input
            type="number"
            min={1}
            max={5000}
            placeholder={String(LIMIT_DEFAULT)}
            value={limitInput}
            onChange={(event) => setLimitInput(event.target.value)}
          />
        </Field>
      </div>

      <div className="mt-4">
        <Button type="submit" disabled={busy}>
          {busy ? "Starting…" : "Start search"}
        </Button>
      </div>

      <fieldset className="mt-6 border-t border-line pt-4">
        <legend className="text-sm font-medium">Presets</legend>
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <Field label="Preset name" className="min-w-48 flex-1">
            <Input
              type="text"
              value={presetName}
              maxLength={80}
              onChange={(event) => setPresetName(event.target.value)}
            />
          </Field>
          <Button type="button" variant="secondary" onClick={() => void savePreset()} disabled={busy}>
            Save preset
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" variant="secondary">
                Load preset
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              {presets.length === 0 ? (
                <DropdownMenuItem disabled>No saved presets.</DropdownMenuItem>
              ) : (
                presets.map((preset) => (
                  <DropdownMenuItem key={preset.id} onSelect={() => loadPreset(preset)}>
                    {preset.name}
                  </DropdownMenuItem>
                ))
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </fieldset>
    </form>
  );
}
