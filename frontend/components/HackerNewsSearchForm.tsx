"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { createHackerNewsJob, startJob } from "../lib/api";
import { useGeo } from "../lib/queries";
import { hackerNewsCriteriaError } from "../lib/schemas";
import { emptyHackerNewsCriteria, type GeoCatalog, type HackerNewsCriteria } from "../lib/types";
import { ChipMultiSelect } from "./ChipMultiSelect";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Field } from "./ui/field";
import { Input } from "./ui/input";

const DEFAULTS = emptyHackerNewsCriteria();

function parseKeywords(value: string): string[] {
  return value
    .split(",")
    .map((part) => part.trim())
    .filter(Boolean);
}

export function HackerNewsSearchForm({ initialGeo }: { initialGeo?: GeoCatalog | undefined }) {
  const router = useRouter();
  const geoQuery = useGeo(initialGeo);
  const catalog = geoQuery.data ?? null;
  const [countries, setCountries] = useState<string[]>([]);
  const [monthsInput, setMonthsInput] = useState("");
  const [keywordText, setKeywordText] = useState("");
  const [remoteOnly, setRemoteOnly] = useState(DEFAULTS.remote_only);
  const [githubLookup, setGithubLookup] = useState(DEFAULTS.github_lookup);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const shownError =
    error ??
    (geoQuery.error
      ? geoQuery.error instanceof Error
        ? geoQuery.error.message
        : "Could not load the location list."
      : null);

  function currentCriteria(): HackerNewsCriteria {
    return {
      months_back: monthsInput.trim() === "" ? DEFAULTS.months_back : Number(monthsInput),
      countries,
      keywords: parseKeywords(keywordText),
      remote_only: remoteOnly,
      github_lookup: githubLookup,
    };
  }

  async function startSearch() {
    const next = currentCriteria();
    const problem = hackerNewsCriteriaError(next);
    if (problem) {
      setError(problem);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const job = await createHackerNewsJob(next);
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
        <h1 className="text-lg font-semibold tracking-tight">Search Hacker News hiring threads</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Reads the monthly “Ask HN: Who wants to be hired?” threads. Each post becomes a contact
          with the email, LinkedIn, GitHub and location its author published. Filters are optional:
          leave them empty to keep every post that has a contact.
        </p>
      </div>

      {shownError ? <p className="mb-4 text-sm text-danger">{shownError}</p> : null}

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <ChipMultiSelect
          label="Location"
          helper="Optional. Keep only posts whose location resolves to these countries"
          placeholder="Search and add locations..."
          options={catalog?.countries ?? []}
          value={countries}
          onChange={setCountries}
          countNoun="location"
        />
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Months back">
            <Input
              type="number"
              min={1}
              max={24}
              placeholder={String(DEFAULTS.months_back)}
              value={monthsInput}
              onChange={(event) => setMonthsInput(event.target.value)}
            />
          </Field>
          <Field label="Keywords">
            <Input
              type="text"
              value={keywordText}
              placeholder="Optional, comma-separated"
              onChange={(event) => setKeywordText(event.target.value)}
            />
          </Field>
        </div>
      </div>
      <p className="mt-1 text-xs text-muted">
        One thread is posted each month. Keywords are matched against the Technologies line and
        the post text.
      </p>

      <div className="mt-4 flex flex-col gap-2">
        <label className="inline-flex items-center gap-2 text-sm text-ink">
          <Checkbox
            checked={remoteOnly}
            onCheckedChange={(checked) => setRemoteOnly(checked === true)}
          />
          Remote only
        </label>
        <label className="inline-flex items-center gap-2 text-sm text-ink">
          <Checkbox
            checked={githubLookup}
            onCheckedChange={(checked) => setGithubLookup(checked === true)}
          />
          Look up linked GitHub profiles for contacts without a personal email
        </label>
      </div>

      <div className="mt-4">
        <Button type="submit" disabled={busy}>
          {busy ? "Starting…" : "Start search"}
        </Button>
      </div>
    </form>
  );
}
