import { Suspense } from "react";

import { ContactsBrowser } from "../../components/ContactsBrowser";

export default function ContactsPage() {
  return (
    <Suspense fallback={<p className="text-sm text-muted">Loading contacts…</p>}>
      <ContactsBrowser />
    </Suspense>
  );
}
