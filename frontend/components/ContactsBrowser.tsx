"use client";

import { Download } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";

import {
  bulkUpdateStatus,
  createOutreachStatus,
  deleteOutreachStatus,
  downloadContacts,
  listContactCountries,
  listContactLogins,
  listContacts,
  listJobs,
  listOutreachStatuses,
  renameOutreachStatus,
  updateContactStatus,
} from "../lib/api";
import type { Contact, ContactQuery, JobSummary, OutreachStatus } from "../lib/types";
import { cn } from "../lib/utils";
import { ChipMultiSelect } from "./ChipMultiSelect";
import { MessageComposer } from "./MessageComposer";
import { StatusMultiSelect } from "./StatusMultiSelect";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { ChoiceSelect } from "./ui/choice-select";
import { Field } from "./ui/field";
import { Input } from "./ui/input";

const PAGE_SIZES = [25, 50, 100] as const;
const NOT_SET_LABEL = "Not set";

function readQuery(params: URLSearchParams): ContactQuery {
  const page = Number(params.get("page") || "1");
  const pageSize = Number(params.get("page_size") || "25");
  const email = params.get("email");
  const emailKind = params.get("email_kind");
  const linkedin = params.get("linkedin");
  return {
    page: Number.isFinite(page) && page > 0 ? page : 1,
    page_size: pageSize === 50 || pageSize === 100 ? pageSize : 25,
    email: email === "has" || email === "missing" ? email : "any",
    email_kind: emailKind === "personal" || emailKind === "other" ? emailKind : "any",
    linkedin: linkedin === "has" || linkedin === "missing" ? linkedin : "any",
    status: params.getAll("status"),
    country: params.getAll("country"),
    q: params.get("q") || "",
    job_id: params.get("job_id") || "",
  };
}

function kindLabel(kind: "personal" | "other"): string {
  return kind === "personal" ? "Personal" : "Work";
}

function emailFilterValue(query: ContactQuery): string {
  if (query.email === "missing") {
    return "missing";
  }
  if (query.email_kind === "personal" || query.email_kind === "other") {
    return query.email_kind;
  }
  return "any";
}

export function ContactsBrowser() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const query = useMemo(() => readQuery(searchParams), [searchParams]);
  const queryKey = searchParams.toString();
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [total, setTotal] = useState(0);
  const [statuses, setStatuses] = useState<OutreachStatus[]>([]);
  const [countries, setCountries] = useState<string[]>([]);
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [bulkChoice, setBulkChoice] = useState("");
  const [newStatus, setNewStatus] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loadedKey, setLoadedKey] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [composing, setComposing] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const loading = loadedKey !== queryKey;

  useEffect(() => {
    let cancelled = false;
    listOutreachStatuses()
      .then((next) => {
        if (!cancelled) {
          setStatuses(next);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Could not load statuses.");
        }
      });
    listJobs()
      .then((next) => {
        if (!cancelled) {
          setJobs(next);
        }
      })
      .catch(() => undefined);
    listContactCountries()
      .then((next) => {
        if (!cancelled) {
          setCountries(next);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Could not load locations.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const current = readQuery(new URLSearchParams(queryKey));
    listContacts(current)
      .then((page) => {
        if (!cancelled) {
          setContacts(page.contacts);
          setTotal(page.total);
          setError(null);
          setLoadedKey(queryKey);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setContacts([]);
          setTotal(0);
          setLoadedKey(queryKey);
          setError(reason instanceof Error ? reason.message : "Could not load contacts.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [queryKey]);

  function replace(updates: Record<string, string | string[] | null>, resetPage: boolean) {
    const next = new URLSearchParams(searchParams.toString());
    for (const [key, value] of Object.entries(updates)) {
      next.delete(key);
      if (Array.isArray(value)) {
        for (const item of value) {
          if (item) {
            next.append(key, item);
          }
        }
      } else if (value) {
        next.set(key, value);
      }
    }
    if (resetPage) {
      next.delete("page");
    }
    const text = next.toString();
    router.replace(text ? `/contacts?${text}` : "/contacts");
  }

  const visible = contacts.map((contact) => contact.login);
  const allVisible = visible.length > 0 && visible.every((login) => selected.has(login));
  const someVisible = visible.some((login) => selected.has(login));
  const pages = Math.max(1, Math.ceil(total / (query.page_size || 25)));
  const labels = new Map(statuses.map((status) => [status.id, status.label]));
  const selectedCountries = (query.country ?? []).filter(Boolean);
  const locationOptions = [
    NOT_SET_LABEL,
    ...[...new Set(countries.concat(selectedCountries.filter((country) => country !== "none")))].sort(
      (left, right) => left.localeCompare(right),
    ),
  ];
  const locationValue = selectedCountries.map((country) =>
    country === "none" ? NOT_SET_LABEL : country,
  );

  function togglePage() {
    setSelected((current) => {
      const next = new Set(current);
      if (allVisible) {
        for (const login of visible) {
          next.delete(login);
        }
      } else {
        for (const login of visible) {
          next.add(login);
        }
      }
      return next;
    });
  }

  function toggleOne(login: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(login)) {
        next.delete(login);
      } else {
        next.add(login);
      }
      return next;
    });
  }

  async function selectMatching() {
    setBusy(true);
    setError(null);
    try {
      const logins = await listContactLogins(query);
      setSelected((current) => new Set([...current, ...logins]));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not select matching contacts.");
    } finally {
      setBusy(false);
    }
  }

  async function applyBulk() {
    if (!bulkChoice || selected.size === 0) {
      return;
    }
    setBusy(true);
    setError(null);
    const status = bulkChoice === "none" ? null : bulkChoice;
    const logins = [...selected];
    try {
      await bulkUpdateStatus(logins, status);
      setContacts((current) =>
        current.map((contact) =>
          selected.has(contact.login) ? { ...contact, outreach_status: status } : contact,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not update the selected contacts.");
    } finally {
      setBusy(false);
    }
  }

  async function changeRow(login: string, value: string) {
    const status = value === "none" ? null : value;
    const previous = contacts;
    setContacts((current) =>
      current.map((contact) =>
        contact.login === login ? { ...contact, outreach_status: status } : contact,
      ),
    );
    try {
      await updateContactStatus(login, status);
    } catch (reason) {
      setContacts(previous);
      setError(reason instanceof Error ? reason.message : "Could not update the status.");
    }
  }

  async function addStatus() {
    const label = newStatus.trim();
    if (!label) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await createOutreachStatus(label);
      setStatuses((current) => [...current, created]);
      setNewStatus("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not add the status.");
    } finally {
      setBusy(false);
    }
  }

  async function renameStatus(statusId: string, label: string) {
    const cleaned = label.trim();
    const current = statuses.find((status) => status.id === statusId);
    if (!cleaned || !current || cleaned === current.label) {
      return;
    }
    setError(null);
    try {
      const renamed = await renameOutreachStatus(statusId, cleaned);
      setStatuses((items) => items.map((status) => (status.id === statusId ? renamed : status)));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not rename the status.");
    }
  }

  async function removeStatus(status: OutreachStatus) {
    if (!window.confirm(`Delete “${status.label}”? Contacts using it return to Not set.`)) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await deleteOutreachStatus(status.id);
      setStatuses((items) => items.filter((item) => item.id !== status.id));
      setContacts((items) =>
        items.map((contact) =>
          contact.outreach_status === status.id ? { ...contact, outreach_status: null } : contact,
        ),
      );
      replace(
        { status: (query.status ?? []).filter((item) => item !== status.id) },
        false,
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Could not delete the status.");
    } finally {
      setBusy(false);
    }
  }

  function toggleStatusFilter(statusId: string) {
    const current = new Set(query.status ?? []);
    if (current.has(statusId)) {
      current.delete(statusId);
    } else {
      current.add(statusId);
    }
    replace({ status: [...current] }, true);
  }

  function downloadSelected() {
    setBusy(true);
    setError(null);
    downloadContacts([...selected])
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "Could not download the CSV.");
      })
      .finally(() => setBusy(false));
  }

  return (
    <section className="rounded-panel border border-line bg-white p-4 shadow-panel">
      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <h1 className="text-lg font-semibold tracking-tight">Contacts</h1>
          <Badge>{total} matching</Badge>
        </div>
        <Button
          type="button"
          variant="secondary"
          disabled={busy || selected.size === 0}
          onClick={downloadSelected}
        >
          <Download className="size-4" />
          Download
        </Button>
      </div>
      {error ? <p className="mb-3 text-sm text-danger">{error}</p> : null}
      <form
        onSubmit={(event) => {
          event.preventDefault();
          replace({ q: searchRef.current?.value.trim() || null }, true);
        }}
      >
        <div className="grid grid-cols-2 items-start gap-3 min-[721px]:grid-cols-4">
          <div className="col-span-1 min-[721px]:col-span-2">
            <ChipMultiSelect
              label="Location"
              placeholder="Search and add locations..."
              options={locationOptions}
              value={locationValue}
              onChange={(next) =>
                replace(
                  {
                    country: next.map((item) => (item === NOT_SET_LABEL ? "none" : item)),
                  },
                  true,
                )
              }
              countNoun="location"
            />
          </div>
          <Field label="Search">
            <Input
              ref={searchRef}
              key={query.q || ""}
              type="search"
              defaultValue={query.q || ""}
              placeholder="Username, name, or email"
            />
          </Field>
          <ChoiceSelect
            label="Public email"
            value={emailFilterValue(query)}
            onValueChange={(value) => {
              if (value === "personal" || value === "other") {
                replace({ email: null, email_kind: value }, true);
                return;
              }
              replace({ email: value === "any" ? null : value, email_kind: null }, true);
            }}
            options={[
              { value: "any", label: "Any" },
              { value: "personal", label: "Personal" },
              { value: "other", label: "Work" },
              { value: "missing", label: "None" },
            ]}
          />
          <ChoiceSelect
            label="LinkedIn"
            value={query.linkedin || "any"}
            onValueChange={(value) => replace({ linkedin: value }, true)}
            options={[
              { value: "any", label: "Any" },
              { value: "has", label: "Has one" },
              { value: "missing", label: "Has none" },
            ]}
          />
          <StatusMultiSelect
            options={[
              { id: "none", label: "Not set" },
              ...statuses.map((status) => ({ id: status.id, label: status.label })),
            ]}
            value={query.status ?? []}
            onToggle={toggleStatusFilter}
          />
          <ChoiceSelect
            label="Search job"
            value={query.job_id || "any"}
            onValueChange={(value) => replace({ job_id: value === "any" ? null : value }, true)}
            options={[
              { value: "any", label: "Any search" },
              ...jobs.map((job) => ({
                value: job.id,
                label: `${job.created_at ? job.created_at.slice(0, 16) : job.id.slice(0, 8)} · ${job.status}`,
              })),
            ]}
          />
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button type="submit">Search</Button>
          <details
            className="[&:not([open])>:not(summary)]:hidden open:basis-full open:rounded-control open:border open:border-line open:bg-header open:p-3"
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
              }
            }}
          >
            <summary className="cursor-pointer text-sm font-medium text-ink">Edit statuses</summary>
            <ul className="mt-3 flex flex-col gap-2">
              {statuses.map((status) => (
                <li key={status.id} className="flex items-center gap-2">
                  <Input
                    type="text"
                    defaultValue={status.label}
                    aria-label={`Rename ${status.label}`}
                    onBlur={(event) => void renameStatus(status.id, event.target.value)}
                  />
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={busy}
                    onClick={() => void removeStatus(status)}
                  >
                    Delete
                  </Button>
                </li>
              ))}
            </ul>
            <div className="mt-3 flex flex-wrap items-end gap-2">
              <Field label="New status" className="min-w-40 flex-1">
                <Input
                  type="text"
                  value={newStatus}
                  maxLength={40}
                  onChange={(event) => setNewStatus(event.target.value)}
                />
              </Field>
              <Button type="button" disabled={busy || !newStatus.trim()} onClick={() => void addStatus()}>
                Add status
              </Button>
            </div>
          </details>
        </div>
      </form>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <p className="text-sm text-muted">
          {total} matching
          {selected.size > 0 ? ` · ${selected.size} selected` : ""}
        </p>
        {selected.size > 0 ? (
          <>
            <div className="min-w-40">
              <ChoiceSelect
                ariaLabel="Bulk status"
                value={bulkChoice || "choose"}
                onValueChange={(value) => setBulkChoice(value === "choose" ? "" : value)}
                options={[
                  { value: "choose", label: "Choose a status" },
                  { value: "none", label: "Not set" },
                  ...statuses.map((status) => ({ value: status.id, label: status.label })),
                ]}
              />
            </div>
            <Button type="button" disabled={busy || bulkChoice === ""} onClick={() => void applyBulk()}>
              Set status
            </Button>
            <Button type="button" disabled={busy} onClick={() => setComposing(true)}>
              Message
            </Button>
            <Button
              type="button"
              variant="secondary"
              disabled={busy || total === 0}
              onClick={() => void selectMatching()}
            >
              Select all matching
            </Button>
            <Button type="button" variant="secondary" onClick={() => setSelected(new Set())}>
              Clear
            </Button>
          </>
        ) : null}
        <label className="ml-auto flex items-center gap-2 text-[13px] font-medium text-muted">
          Per page
          <ChoiceSelect
            ariaLabel="Per page"
            value={String(query.page_size || 25)}
            onValueChange={(value) => replace({ page_size: value }, true)}
            triggerClassName="w-20"
            options={PAGE_SIZES.map((size) => ({ value: String(size), label: String(size) }))}
          />
        </label>
      </div>
      <MessageComposer
        open={composing}
        selected={[...selected]}
        contacts={contacts}
        onClose={() => setComposing(false)}
        onDelivered={() => {
          listContacts(readQuery(new URLSearchParams(queryKey)))
            .then((page) => {
              setContacts(page.contacts);
              setTotal(page.total);
            })
            .catch(() => undefined);
        }}
      />
      <div className="mt-2 overflow-auto rounded-panel border border-line">
        <table className="w-full min-w-[960px] border-collapse text-sm">
          <thead className="sticky top-0 z-10 bg-header text-left text-xs font-medium text-muted">
            <tr className="border-b border-line">
              <th className="w-10 px-3 py-2">
                <Checkbox
                  checked={allVisible ? true : someVisible ? "indeterminate" : false}
                  onCheckedChange={() => togglePage()}
                  aria-label="Select all contacts on this page"
                />
              </th>
              <th className="px-3 py-2 font-medium">Username</th>
              <th className="px-3 py-2 font-medium">Name</th>
              <th className="px-3 py-2 font-medium">Public email</th>
              <th className="px-3 py-2 font-medium">Kind</th>
              <th className="px-3 py-2 font-medium">LinkedIn</th>
              <th className="px-3 py-2 font-medium">Country</th>
              <th className="px-3 py-2 font-medium">Status</th>
              <th className="px-3 py-2 font-medium">Last checked</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td className="px-3 py-6 text-muted" colSpan={9}>
                  Loading contacts…
                </td>
              </tr>
            ) : contacts.length === 0 ? (
              <tr>
                <td className="px-3 py-6 text-muted" colSpan={9}>
                  No contacts match these filters.
                </td>
              </tr>
            ) : (
              contacts.map((contact, index) => {
                const rowStatus = contact.outreach_status ?? "none";
                const statusOptions = [
                  { value: "none", label: "Not set" },
                  ...(contact.outreach_status && !labels.has(contact.outreach_status)
                    ? [{ value: contact.outreach_status, label: contact.outreach_status }]
                    : []),
                  ...statuses.map((status) => ({ value: status.id, label: status.label })),
                ];
                return (
                  <tr
                    key={contact.login}
                    className={cn(
                      "border-b border-line last:border-0 hover:bg-hover",
                      index % 2 === 1 && "bg-stripe",
                      selected.has(contact.login) &&
                        "bg-[color-mix(in_srgb,#5b5bd6_7%,white)]",
                    )}
                  >
                    <td className="px-3 py-2">
                      <Checkbox
                        checked={selected.has(contact.login)}
                        onCheckedChange={() => toggleOne(contact.login)}
                        aria-label={`Select ${contact.login}`}
                      />
                    </td>
                    <td className="px-3 py-2">
                      <a href={contact.profile_url || `https://github.com/${contact.login}`}>
                        {contact.login}
                      </a>
                    </td>
                    <td className="px-3 py-2">{contact.name || "—"}</td>
                    <td className="px-3 py-2">{contact.public_email || "—"}</td>
                    <td className="px-3 py-2">{contact.email_kinds.map(kindLabel).join(", ") || "—"}</td>
                    <td className="px-3 py-2">
                      {contact.linkedin_url ? <a href={contact.linkedin_url}>Profile</a> : "—"}
                    </td>
                    <td className="px-3 py-2">{contact.country || "—"}</td>
                    <td className="px-3 py-2">
                      <ChoiceSelect
                        ariaLabel={`Status for ${contact.login}`}
                        value={rowStatus}
                        onValueChange={(value) => void changeRow(contact.login, value)}
                        triggerClassName="min-w-32"
                        options={statusOptions}
                      />
                    </td>
                    <td className="px-3 py-2 text-muted">
                      {contact.last_checked_at ? contact.last_checked_at.slice(0, 16) : "—"}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <div className="mt-3 flex items-center justify-end gap-2">
        <Button
          type="button"
          variant="secondary"
          disabled={(query.page || 1) <= 1}
          onClick={() => replace({ page: String((query.page || 1) - 1) }, false)}
        >
          Previous
        </Button>
        <p className="text-sm text-muted">
          Page {query.page} of {pages}
        </p>
        <Button
          type="button"
          variant="secondary"
          disabled={(query.page || 1) >= pages}
          onClick={() => replace({ page: String((query.page || 1) + 1) }, false)}
        >
          Next
        </Button>
      </div>
    </section>
  );
}
