"use client";

import { useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { createJob, createPreset, getGeo, listPresets, startJob } from "../lib/api";
import { UNITED_STATES, emptyCriteria, type Preset, type SearchCriteria } from "../lib/types";

function ageInMonths(years: number, months: number): number {
  return years * 12 + months;
}

function criteriaError(criteria: SearchCriteria): string | null {
  if (criteria.countries.length === 0) {
    return "Select at least one country.";
  }
  const age = criteria.account_age;
  const fields = [age.min_years, age.min_months, age.max_years, age.max_months];
  if (fields.some((value) => !Number.isInteger(value) || value < 0)) {
    return "Account age must be a whole number of years and months.";
  }
  if (age.min_months > 11 || age.max_months > 11 || age.min_years > 100 || age.max_years > 100) {
    return "Years must be 0 to 100 and months must be 0 to 11.";
  }
  if (ageInMonths(age.min_years, age.min_months) > ageInMonths(age.max_years, age.max_months)) {
    return "Minimum account age must be less than or equal to the maximum.";
  }
  const limit = criteria.candidate_limit;
  if (!Number.isInteger(limit) || limit < 1 || limit > 5000) {
    return "Candidate limit must be between 1 and 5000.";
  }
  return null;
}

function parseKeywords(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

export function SearchForm() {
  const router = useRouter();
  const [catalog, setCatalog] = useState<{ countries: string[]; us_states: string[] } | null>(null);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [criteria, setCriteria] = useState<SearchCriteria>(emptyCriteria);
  const [keywordText, setKeywordText] = useState("");
  const [countryQuery, setCountryQuery] = useState("");
  const [stateQuery, setStateQuery] = useState("");
  const [presetName, setPresetName] = useState("");
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

  const filteredCountries = useMemo(() => {
    const query = countryQuery.trim().toLowerCase();
    return (catalog?.countries ?? []).filter((country) => country.toLowerCase().includes(query));
  }, [catalog, countryQuery]);

  const filteredStates = useMemo(() => {
    const query = stateQuery.trim().toLowerCase();
    return (catalog?.us_states ?? []).filter((state) => state.toLowerCase().includes(query));
  }, [catalog, stateQuery]);

  const unitedStatesSelected = criteria.countries.includes(UNITED_STATES);

  function currentCriteria(): SearchCriteria {
    return {
      ...criteria,
      us_states: unitedStatesSelected ? criteria.us_states : [],
      keywords: parseKeywords(keywordText),
    };
  }

  function toggleCountry(country: string) {
    setCriteria((current) => {
      const selected = current.countries.includes(country)
        ? current.countries.filter((item) => item !== country)
        : [...current.countries, country];
      return {
        ...current,
        countries: selected,
        us_states: selected.includes(UNITED_STATES) ? current.us_states : [],
      };
    });
  }

  function toggleState(state: string) {
    setCriteria((current) => {
      const selected = current.us_states.includes(state)
        ? current.us_states.filter((item) => item !== state)
        : [...current.us_states, state];
      return { ...current, us_states: selected };
    });
  }

  function loadPreset(preset: Preset) {
    setCriteria(preset.criteria);
    setKeywordText(preset.criteria.keywords.join(", "));
    setError(null);
  }

  async function savePreset() {
    const next = currentCriteria();
    const problem = criteriaError(next);
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
    const problem = criteriaError(next);
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
      className="panel"
      onSubmit={(event) => {
        event.preventDefault();
        void startSearch();
      }}
    >
      <div className="panel-head">
        <h1>Search public GitHub profiles</h1>
        <p>
          Location and account age seed the search. Contact fields are filled only from what a
          developer has published on their profile or in a public README.
        </p>
      </div>

      {error ? <p className="error">{error}</p> : null}

      <fieldset>
        <legend>Countries</legend>
        <input
          type="search"
          value={countryQuery}
          placeholder="Filter countries"
          aria-label="Filter countries"
          onChange={(event) => setCountryQuery(event.target.value)}
        />
        <div className="choice-list" role="group" aria-label="Countries">
          {filteredCountries.map((country) => (
            <label key={country}>
              <input
                type="checkbox"
                checked={criteria.countries.includes(country)}
                onChange={() => toggleCountry(country)}
              />
              {country}
            </label>
          ))}
        </div>
      </fieldset>

      {unitedStatesSelected ? (
        <fieldset>
          <legend>US states</legend>
          <input
            type="search"
            value={stateQuery}
            placeholder="Filter states"
            aria-label="Filter US states"
            onChange={(event) => setStateQuery(event.target.value)}
          />
          <div className="choice-list" role="group" aria-label="US states">
            {filteredStates.map((state) => (
              <label key={state}>
                <input
                  type="checkbox"
                  checked={criteria.us_states.includes(state)}
                  onChange={() => toggleState(state)}
                />
                {state}
              </label>
            ))}
          </div>
        </fieldset>
      ) : null}

      <fieldset>
        <legend>Account age</legend>
        <div className="age-grid">
          <label>
            Minimum years
            <input
              type="number"
              min={0}
              max={100}
              value={criteria.account_age.min_years}
              onChange={(event) =>
                setCriteria((current) => ({
                  ...current,
                  account_age: { ...current.account_age, min_years: Number(event.target.value) },
                }))
              }
            />
          </label>
          <label>
            Minimum months
            <input
              type="number"
              min={0}
              max={11}
              value={criteria.account_age.min_months}
              onChange={(event) =>
                setCriteria((current) => ({
                  ...current,
                  account_age: { ...current.account_age, min_months: Number(event.target.value) },
                }))
              }
            />
          </label>
          <label>
            Maximum years
            <input
              type="number"
              min={0}
              max={100}
              value={criteria.account_age.max_years}
              onChange={(event) =>
                setCriteria((current) => ({
                  ...current,
                  account_age: { ...current.account_age, max_years: Number(event.target.value) },
                }))
              }
            />
          </label>
          <label>
            Maximum months
            <input
              type="number"
              min={0}
              max={11}
              value={criteria.account_age.max_months}
              onChange={(event) =>
                setCriteria((current) => ({
                  ...current,
                  account_age: { ...current.account_age, max_months: Number(event.target.value) },
                }))
              }
            />
          </label>
        </div>
      </fieldset>

      <label>
        Keywords
        <input
          type="text"
          value={keywordText}
          placeholder="Optional, comma-separated"
          onChange={(event) => setKeywordText(event.target.value)}
        />
      </label>

      <label>
        Candidate limit
        <input
          type="number"
          min={1}
          max={5000}
          value={criteria.candidate_limit}
          onChange={(event) =>
            setCriteria((current) => ({ ...current, candidate_limit: Number(event.target.value) }))
          }
        />
      </label>

      <div className="actions">
        <button type="submit" disabled={busy}>
          {busy ? "Starting…" : "Start search"}
        </button>
      </div>

      <fieldset>
        <legend>Presets</legend>
        <div className="preset-row">
          <label>
            Preset name
            <input
              type="text"
              value={presetName}
              maxLength={80}
              onChange={(event) => setPresetName(event.target.value)}
            />
          </label>
          <button type="button" onClick={() => void savePreset()} disabled={busy}>
            Save preset
          </button>
        </div>
        {presets.length === 0 ? (
          <p className="muted">No saved presets.</p>
        ) : (
          <ul className="preset-list">
            {presets.map((preset) => (
              <li key={preset.id}>
                <button type="button" onClick={() => loadPreset(preset)}>
                  {preset.name}
                </button>
              </li>
            ))}
          </ul>
        )}
      </fieldset>
    </form>
  );
}
