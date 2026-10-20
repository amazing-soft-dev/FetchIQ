"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { exportHref, getJob, postJobAction } from "../lib/api";
import type { Job, JobStatus } from "../lib/types";

const ACTIVE: JobStatus[] = ["queued", "running", "paused"];

export function JobPanel({ jobId }: { jobId: string }) {
  const [job, setJob] = useState<Job | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [includeNoContact, setIncludeNoContact] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      try {
        const next = await getJob(jobId);
        if (!cancelled) {
          setJob(next);
          setError(null);
        }
      } catch (reason) {
        if (!cancelled) {
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
    setError(null);
    try {
      setJob(await postJobAction(jobId, action));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update the job.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel">
      <div className="panel-head">
        <p className="eyebrow">
          <Link href="/">New search</Link>
        </p>
        <h1>Job status</h1>
      </div>
      {error ? <p className="error">{error}</p> : null}
      {job ? (
        <>
          <p className={`status status-${job.status}`}>
            {job.status}
            {ACTIVE.includes(job.status) ? <span className="pulse" aria-hidden="true" /> : null}
          </p>
          <dl className="counts">
            <div>
              <dt>Discovered</dt>
              <dd>{job.counts.discovered}</dd>
            </div>
            <div>
              <dt>Enriched</dt>
              <dd>{job.counts.enriched}</dd>
            </div>
            <div>
              <dt>Errors</dt>
              <dd>{job.counts.errors}</dd>
            </div>
          </dl>
          {job.incomplete_reasons.length > 0 ? (
            <ul className="reasons">
              {job.incomplete_reasons.map((reason) => (
                <li key={reason}>{reason}</li>
              ))}
            </ul>
          ) : null}
          {job.error ? <p className="error">{job.error}</p> : null}
          <div className="actions">
            {job.status === "queued" ? (
              <button type="button" disabled={busy} onClick={() => void act("start")}>
                Start
              </button>
            ) : null}
            {job.status === "running" ? (
              <button type="button" disabled={busy} onClick={() => void act("pause")}>
                Pause
              </button>
            ) : null}
            {job.status === "paused" || job.status === "incomplete" ? (
              <button type="button" disabled={busy} onClick={() => void act("resume")}>
                Resume
              </button>
            ) : null}
            {job.status !== "succeeded" && job.status !== "failed" && job.status !== "cancelled" ? (
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => void act("cancel")}
              >
                Cancel
              </button>
            ) : null}
          </div>
          {job.counts.discovered > 0 ? (
            <div className="export-box">
              <label>
                <input
                  type="checkbox"
                  checked={includeNoContact}
                  onChange={(event) => setIncludeNoContact(event.target.checked)}
                />
                Include rows with no public contact
              </label>
              <a className="button" href={exportHref(job.id, includeNoContact)}>
                Export CSV
              </a>
            </div>
          ) : null}
        </>
      ) : (
        <p className="muted">Loading job…</p>
      )}
    </section>
  );
}
