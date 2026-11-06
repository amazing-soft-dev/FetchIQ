"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useActiveJob } from "../lib/queries";
import { cn } from "../lib/utils";

export function SearchStatusLink() {
  const pathname = usePathname();
  const job = useActiveJob();

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
