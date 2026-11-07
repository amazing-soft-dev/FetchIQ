import type { Contact } from "./types";

export type ContactSource = Contact["source"];
export type SourceFilter = "any" | ContactSource;

export const SOURCE_LABELS: Record<ContactSource, string> = {
  github: "GitHub",
  hackernews: "Hacker News",
};

export const SOURCE_OPTIONS: { value: SourceFilter; label: string }[] = [
  { value: "any", label: "Any source" },
  { value: "github", label: SOURCE_LABELS.github },
  { value: "hackernews", label: SOURCE_LABELS.hackernews },
];

/** The `source` query parameter as the API accepts it; anything else means any. */
export function parseSource(value: string | null | undefined): SourceFilter {
  return value === "github" || value === "hackernews" ? value : "any";
}

/** Badge labels for a contact, in a fixed order, the record's own source first. */
export function sourceLabels(contact: Pick<Contact, "source" | "sources">): string[] {
  const ordered: ContactSource[] = [contact.source];
  for (const source of contact.sources) {
    if (!ordered.includes(source)) {
      ordered.push(source);
    }
  }
  return ordered.map((source) => SOURCE_LABELS[source]);
}
