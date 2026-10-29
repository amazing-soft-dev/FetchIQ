"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { getJob, listJobs } from "../lib/api";
import type { Job, JobStatus } from "../lib/types";
import { cn } from "../lib/utils";

const ACTIVE: JobStatus[] = ["queued", "running", "paused"];
const POLL_MS = 4000;

export function SearchStatusLink() {
  const pathname = usePathname();
  const [job, setJob] = useState<Job | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function load() {
      if (document.hidden) {
        return;
      }
      try {
        const summary = (await listJobs()).find((item) => ACTIVE.includes(item.status));
        const next = summary ? await getJob(summary.id) : null;
        if (!cancelled) {
          setJob(next && ACTIVE.includes(next.status) ? next : null);
        }
      } catch {
        // The header link is a convenience; keep the last known state.
      }
    }

    void load();
    const timer = window.setInterval(() => {
      void load();
    }, POLL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  if (!job || pathname === `/jobs/${job.id}`) {
    return null;
  }

  const paused = job.status === "paused";
  const progress =
    job.counts.discovered > 0 ? `${job.counts.enriched}/${job.counts.discovered}` : null;

  return (
    <Link
      href={`/jobs/${job.id}`}
      className="inline-flex h-8 items-center gap-2 rounded-control border border-line bg-white px-2.5 text-sm text-ink hover:bg-hover"
      title="View the current search status"
    >
      <span
        className={cn("size-2 rounded-full", paused ? "bg-muted" : "animate-pulse bg-accent")}
        aria-hidden="true"
      />
      <span className="capitalize">Search {job.status}</span>
      {progress ? <span className="tabular-nums text-muted">{progress}</span> : null}
    </Link>
  );
}
