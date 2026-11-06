"use client";

import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { exportHref } from "../lib/api";
import { ACTIVE_JOB_STATUSES, useJob, useJobAction, useJobAnalysis } from "../lib/queries";
import type { Job, JobStatus } from "../lib/types";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";

const tone: Record<JobStatus, string> = {
  queued: "text-muted",
  running: "text-accent",
  paused: "text-ink",
  incomplete: "text-ink",
  succeeded: "text-ink",
  failed: "text-danger",
  cancelled: "text-muted",
};

function message(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

export function JobPanel({
  jobId,
  initialJob,
}: {
  jobId: string;
  initialJob?: Job | undefined;
}) {
  const router = useRouter();
  const jobQuery = useJob(jobId, { initialData: initialJob });
  const job = jobQuery.data ?? null;
  const analysisQuery = useJobAnalysis(jobId, job?.status);
  const shownAnalysis = analysisQuery.data ?? null;
  const action = useJobAction(jobId);
  const [actionError, setActionError] = useState<string | null>(null);
  const [includeNoContact, setIncludeNoContact] = useState(true);
  const busy = action.isPending;
  const loadError = jobQuery.error ? message(jobQuery.error, "Could not load the job.") : null;
  const error = actionError ?? loadError;

  async function act(kind: "start" | "pause" | "resume" | "cancel") {
    setActionError(null);
    try {
      await action.mutateAsync(kind);
      if (kind === "cancel") {
        router.push("/");
      }
    } catch (reason) {
      setActionError(message(reason, "Could not update the job."));
    }
  }

  return (
    <section className="rounded-panel border border-line bg-white p-5 shadow-panel">
      <div className="mb-4">
        <p className="text-xs font-medium tracking-wide text-muted uppercase">
          <Link href="/" className="text-muted hover:text-ink">
            New search
          </Link>
        </p>
        <h1 className="mt-1 text-lg font-semibold tracking-tight">Job status</h1>
      </div>
      {error ? <p className="mb-4 text-sm text-danger">{error}</p> : null}
      {job ? (
        <>
          <AnimatePresence mode="wait">
            <motion.p
              key={job.status}
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -6 }}
              transition={{ duration: 0.2 }}
              className={cn(
                "inline-flex items-center gap-2 text-sm font-medium capitalize",
                tone[job.status],
              )}
            >
              {job.status}
              {ACTIVE_JOB_STATUSES.includes(job.status) ? (
                <span className="size-2 animate-pulse rounded-full bg-accent" aria-hidden="true" />
              ) : null}
            </motion.p>
          </AnimatePresence>
          <dl className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
            {(
              [
                ["Discovered", job.counts.discovered],
                ["Enriched", job.counts.enriched],
                ["Errors", job.counts.errors],
                ["Emails", job.counts.emails],
                ["LinkedIn without email", job.counts.linkedin_without_email],
              ] as const
            ).map(([label, value]) => (
              <div key={label} className="rounded-control border border-line bg-header px-3 py-2">
                <dt className="text-xs text-muted">{label}</dt>
                <dd className="mt-1 text-lg font-semibold tabular-nums">{value}</dd>
              </div>
            ))}
          </dl>
          {job.incomplete_reasons.length > 0 ? (
            <ul className="mt-4 list-disc pl-5 text-sm text-muted">
              {job.incomplete_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
          {job.error ? <p className="mt-4 text-sm text-danger">{job.error}</p> : null}
          {job.overlap_preview && job.overlap_preview.fresh > 0 && job.status === "queued" ? (
            <div className="mt-4 rounded-control border border-line bg-header px-3 py-3 text-sm">
              <p>
                {job.overlap_preview.fresh} matching profiles were checked in the last 14 days.
                Start is what spends the GitHub requests. You can widen the account age or pick
                another country instead.
              </p>
              <ul className="mt-2 list-disc pl-5 text-muted">
                {job.overlap_preview.jobs.map((earlier) => (
                  <li key={earlier.id}>
                    <Link href={`/jobs/${earlier.id}`}>{earlier.id}</Link>: {earlier.matched} of{" "}
                    {earlier.live} profiles match a country in this search, {earlier.fresh} checked
                    recently.
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {shownAnalysis ? (
            <div className="mt-4 text-sm">
              <p>{shownAnalysis.headline}</p>
              <p className="mt-2 text-muted">
                Slots used {shownAnalysis.slots} of {shownAnalysis.limit}. New {shownAnalysis.inserted}
                {shownAnalysis.updated === null ? "" : `, updated ${shownAnalysis.updated}`}.
                {shownAnalysis.duplicates === null
                  ? ""
                  : ` Duplicates with nothing new: ${shownAnalysis.duplicates}.`}
                {shownAnalysis.shortfall > 0 ? ` Shortfall ${shownAnalysis.shortfall}.` : ""}
                {shownAnalysis.moved_out !== null && shownAnalysis.moved_out > 0
                  ? ` ${shownAnalysis.moved_out} profiles have since moved to a later search.`
                  : ""}
              </p>
              {shownAnalysis.places_unknown > 0 ? (
                <p className="mt-2 text-muted">
                  GitHub totals are known for {shownAnalysis.places_known} places.{" "}
                  {shownAnalysis.places_unknown} places did not record a full-window total.
                </p>
              ) : null}
              {shownAnalysis.suggestions.length > 0 ? (
                <ul className="mt-2 list-disc pl-5 text-muted">
                  {shownAnalysis.suggestions.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              ) : null}
              {shownAnalysis.uncollected_countries.length > 0 ? (
                <details className="mt-2 text-muted">
                  <summary>
                    {shownAnalysis.uncollected_countries.length} countries have no overlapping search
                    in this window.
                  </summary>
                  <p className="mt-1">{shownAnalysis.uncollected_countries.join(", ")}</p>
                </details>
              ) : null}
            </div>
          ) : null}
          <div className="mt-4 flex flex-wrap gap-2">
            {job.status === "queued" ? (
              <Button type="button" disabled={busy} onClick={() => void act("start")}>
                Start
              </Button>
            ) : null}
            {job.status === "running" ? (
              <Button type="button" disabled={busy} onClick={() => void act("pause")}>
                Pause
              </Button>
            ) : null}
            {job.status === "paused" ||
            job.status === "incomplete" ||
            (job.status === "failed" && (job.error ?? "").startsWith("Could not resolve")) ? (
              <Button type="button" disabled={busy} onClick={() => void act("resume")}>
                Resume
              </Button>
            ) : null}
            {job.status !== "succeeded" && job.status !== "failed" && job.status !== "cancelled" ? (
              <Button
                type="button"
                variant="secondary"
                disabled={busy}
                onClick={() => void act("cancel")}
              >
                Cancel
              </Button>
            ) : null}
          </div>
          {job.counts.discovered > 0 ? (
            <>
              <p className="mt-4 text-sm text-muted">
                {job.counts.enriched} of {job.counts.discovered} profiles enriched. Profile name,
                location, public email, and UTC offset are filled as each profile is enriched.
              </p>
              <p className="mt-2 text-sm">
                <Link href={`/contacts?job_id=${job.id}`}>Review these contacts</Link>
              </p>
              <div className="mt-4 flex flex-wrap items-center gap-3">
                <label className="inline-flex items-center gap-2 text-sm text-ink">
                  <Checkbox
                    checked={includeNoContact}
                    onCheckedChange={(checked) => setIncludeNoContact(checked === true)}
                  />
                  Include rows with no public contact
                </label>
                <Button asChild>
                  <a href={exportHref(job.id, includeNoContact)}>Export CSV</a>
                </Button>
              </div>
            </>
          ) : null}
        </>
      ) : (
        <p className="text-sm text-muted">Loading job…</p>
      )}
    </section>
  );
}
