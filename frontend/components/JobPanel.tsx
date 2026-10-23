"use client";

import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { exportHref, getJob, postJobAction } from "../lib/api";
import type { Job, JobStatus } from "../lib/types";
import { cn } from "../lib/utils";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";

const ACTIVE: JobStatus[] = ["queued", "running", "paused"];

const tone: Record<JobStatus, string> = {
  queued: "text-muted",
  running: "text-accent",
  paused: "text-ink",
  incomplete: "text-ink",
  succeeded: "text-ink",
  failed: "text-danger",
  cancelled: "text-muted",
};

export function JobPanel({ jobId }: { jobId: string }) {
  const router = useRouter();
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [includeNoContact, setIncludeNoContact] = useState(true);
  const [busy, setBusy] = useState(false);
  const keepError = useRef(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const next = await getJob(jobId);
        if (!cancelled) {
          setJob(next);
          if (!keepError.current) {
            setError(null);
          }
        }
      } catch (reason) {
        if (!cancelled) {
          keepError.current = false;
          setError(reason instanceof Error ? reason.message : "Could not load the job.");
        }
      }
    }

    void load();
    const timer = window.setInterval(() => {
      void load();
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [jobId]);

  async function act(action: "start" | "pause" | "resume" | "cancel") {
    setBusy(true);
    keepError.current = false;
    setError(null);
    try {
      const next = await postJobAction(jobId, action);
      if (action === "cancel") {
        router.push("/");
        return;
      }
      setJob(next);
    } catch (reason) {
      keepError.current = true;
      setError(reason instanceof Error ? reason.message : "Could not update the job.");
    } finally {
      setBusy(false);
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
              {ACTIVE.includes(job.status) ? (
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
