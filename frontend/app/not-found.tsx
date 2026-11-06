import Link from "next/link";

export default function NotFound() {
  return (
    <section className="rounded-panel border border-line bg-white p-5 shadow-panel">
      <h1 className="text-lg font-semibold tracking-tight">Page not found</h1>
      <p className="mt-2 text-sm text-muted">There is nothing at this address.</p>
      <p className="mt-4 text-sm">
        <Link href="/">Start a new search</Link>
      </p>
    </section>
  );
}
