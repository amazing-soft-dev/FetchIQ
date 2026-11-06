import { Suspense } from "react";

import { ContactsBrowser } from "../../components/ContactsBrowser";
import { loadOutreachStatuses } from "../../lib/server-api";

export default async function ContactsPage() {
  const statuses = await loadOutreachStatuses();
  return (
    <Suspense fallback={<p className="text-sm text-muted">Loading contacts…</p>}>
      <ContactsBrowser initialStatuses={statuses} />
    </Suspense>
  );
}
