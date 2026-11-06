"use client";

import { Button } from "../components/ui/button";

export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <section className="rounded-panel border border-line bg-white p-5 shadow-panel">
      <h1 className="text-lg font-semibold tracking-tight">Something went wrong</h1>
      <p className="mt-2 text-sm text-danger">{error.message || "The page could not be shown."}</p>
      {error.digest ? <p className="mt-1 text-xs text-muted">Reference {error.digest}</p> : null}
      <div className="mt-4">
        <Button type="button" onClick={reset}>
          Try again
        </Button>
      </div>
    </section>
  );
}
