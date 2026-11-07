"use client";

import type { Job } from "../lib/types";

function criteriaSummary(job: Job): string[] {
  const criteria = job.hackernews_criteria;
  if (!criteria) {
    return [];
  }
  const lines = [
    criteria.months_back === 1 ? "Newest thread" : `Newest ${criteria.months_back} threads`,
  ];
  if (criteria.countries.length > 0) {
    lines.push(`Locations: ${criteria.countries.join(", ")}`);
  }
  if (criteria.keywords.length > 0) {
    lines.push(`Keywords: ${criteria.keywords.join(", ")}`);
  }
  if (criteria.remote_only) {
    lines.push("Remote only");
  }
  lines.push(
    criteria.github_lookup
      ? "Linked GitHub profiles are looked up for contacts without a personal email"
      : "No GitHub lookup",
  );
  return lines;
}

/** What a Hacker News job read: its criteria, one row per thread, and the GitHub lookups. */
export function HackerNewsJobDetails({ job }: { job: Job }) {
  const lookups = job.github_lookups;
  const looked = lookups.done + lookups.missing + lookups.errors;
  return (
    <div className="mt-4 text-sm">
      <p className="text-muted">{criteriaSummary(job).join(" · ")}</p>
      {job.threads.length > 0 ? (
        <div className="mt-3 overflow-auto rounded-panel border border-line">
          <table className="w-full min-w-[640px] border-collapse text-sm">
            <thead className="bg-header text-left text-xs font-medium text-muted">
              <tr className="border-b border-line">
                <th className="px-3 py-2 font-medium">Thread</th>
                <th className="px-3 py-2 font-medium">Posts</th>
                <th className="px-3 py-2 font-medium">Stored</th>
                <th className="px-3 py-2 font-medium">Merged with GitHub</th>
                <th className="px-3 py-2 font-medium">Unchanged</th>
                <th className="px-3 py-2 font-medium">Filtered out</th>
                <th className="px-3 py-2 font-medium">No contact</th>
              </tr>
            </thead>
            <tbody>
              {job.threads.map((thread) => (
                <tr key={thread.id} className="border-b border-line last:border-0">
                  <td className="px-3 py-2">
                    <a href={thread.url}>{thread.title}</a>
                  </td>
                  <td className="px-3 py-2 tabular-nums">{thread.posts}</td>
                  <td className="px-3 py-2 tabular-nums">{thread.stored}</td>
                  <td className="px-3 py-2 tabular-nums">{thread.merged}</td>
                  <td className="px-3 py-2 tabular-nums">{thread.unchanged}</td>
                  <td className="px-3 py-2 tabular-nums">{thread.filtered}</td>
                  <td className="px-3 py-2 tabular-nums">{thread.skipped}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="mt-3 text-muted">No thread has been read yet.</p>
      )}
      {job.hackernews_criteria?.github_lookup ? (
        <p className="mt-3 text-muted">
          GitHub lookups: {looked} ({lookups.done} read, {lookups.missing} missing,{" "}
          {lookups.errors} failed). Unchanged posts belong to people stored in the last 14 days or
          already holding an email.
        </p>
      ) : null}
    </div>
  );
}
