import Link from "next/link";

export default function JobNotFound() {
  return (
    <section className="rounded-panel border border-line bg-white p-5 shadow-panel">
      <h1 className="text-lg font-semibold tracking-tight">Job not found</h1>
      <p className="mt-2 text-sm text-muted">
        No search job has this id. It may have been created against another database.
      </p>
      <p className="mt-4 text-sm">
        <Link href="/">Start a new search</Link>
      </p>
    </section>
  );
}
