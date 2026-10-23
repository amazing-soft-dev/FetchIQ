"use client";

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

const PAGE_SIZES = [25, 50, 100] as const;

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
  return kind === "personal" ? "Personal" : "Other";
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
  const selectedCountries = (query.country ?? []).filter((country) => country && country !== "none");
  const locationChoices = [...new Set([...countries, ...selectedCountries])].sort((left, right) =>
    left.localeCompare(right),
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

  function toggleCountryFilter(country: string) {
    const current = new Set(query.country ?? []);
    if (current.has(country)) {
      current.delete(country);
    } else {
      current.add(country);
    }
    replace({ country: [...current] }, true);
  }

  return (
    <section className="panel contacts">
      <div className="panel-head">
        <p className="eyebrow">Stored GitHub profiles</p>
        <h1>Contacts</h1>
      </div>
      {error ? <p className="error">{error}</p> : null}
      <form
        className="contact-filters"
        onSubmit={(event) => {
          event.preventDefault();
          replace({ q: searchRef.current?.value.trim() || null }, true);
        }}
      >
        <label>
          Search
          <input
            ref={searchRef}
            key={query.q || ""}
            type="search"
            defaultValue={query.q || ""}
            placeholder="Username, name, or email"
          />
        </label>
        <label>
          Public email
          <select
            value={query.email}
            onChange={(event) => replace({ email: event.target.value }, true)}
          >
            <option value="any">Any</option>
            <option value="has">Has one</option>
            <option value="missing">Has none</option>
          </select>
        </label>
        <label>
          Email kind
          <select
            value={query.email_kind}
            onChange={(event) => replace({ email_kind: event.target.value }, true)}
          >
            <option value="any">Any</option>
            <option value="personal">Personal</option>
            <option value="other">Other</option>
          </select>
        </label>
        <label>
          LinkedIn
          <select
            value={query.linkedin}
            onChange={(event) => replace({ linkedin: event.target.value }, true)}
          >
            <option value="any">Any</option>
            <option value="has">Has one</option>
            <option value="missing">Has none</option>
          </select>
        </label>
        <label>
          Search job
          <select
            value={query.job_id || ""}
            onChange={(event) => replace({ job_id: event.target.value || null }, true)}
          >
            <option value="">Any search</option>
            {jobs.map((job) => (
              <option key={job.id} value={job.id}>
                {job.created_at ? job.created_at.slice(0, 16) : job.id.slice(0, 8)} · {job.status}
              </option>
            ))}
          </select>
        </label>
        <div className="filter-actions">
          <button type="submit">Search</button>
          <button
            type="button"
            className="secondary"
            onClick={() => replace({ email: "missing", linkedin: "has" }, true)}
          >
            LinkedIn, no email
          </button>
        </div>
      </form>
      <fieldset>
        <legend>Location</legend>
        <div className="choice-list">
          <label>
            <input
              type="checkbox"
              checked={(query.country ?? []).includes("none")}
              onChange={() => toggleCountryFilter("none")}
            />
            Not set
          </label>
          {locationChoices.map((country) => (
            <label key={country}>
              <input
                type="checkbox"
                checked={(query.country ?? []).includes(country)}
                onChange={() => toggleCountryFilter(country)}
              />
              {country}
            </label>
          ))}
        </div>
      </fieldset>
      <fieldset>
        <legend>Status</legend>
        <div className="choice-list">
          <label>
            <input
              type="checkbox"
              checked={(query.status ?? []).includes("none")}
              onChange={() => toggleStatusFilter("none")}
            />
            Not set
          </label>
          {statuses.map((status) => (
            <label key={status.id}>
              <input
                type="checkbox"
                checked={(query.status ?? []).includes(status.id)}
                onChange={() => toggleStatusFilter(status.id)}
              />
              {status.label}
            </label>
          ))}
        </div>
      </fieldset>
      <details className="status-editor">
        <summary>Edit statuses</summary>
        <ul className="status-list">
          {statuses.map((status) => (
            <li key={status.id}>
              <input
                type="text"
                defaultValue={status.label}
                aria-label={`Rename ${status.label}`}
                onBlur={(event) => void renameStatus(status.id, event.target.value)}
              />
              <button
                type="button"
                className="secondary"
                disabled={busy}
                onClick={() => void removeStatus(status)}
              >
                Delete
              </button>
            </li>
          ))}
        </ul>
        <div className="preset-row">
          <label>
            New status
            <input
              type="text"
              value={newStatus}
              maxLength={40}
              onChange={(event) => setNewStatus(event.target.value)}
            />
          </label>
          <button type="button" disabled={busy || !newStatus.trim()} onClick={() => void addStatus()}>
            Add status
          </button>
        </div>
      </details>
      <div className="bulk-bar">
        <p className="muted">
          {selected.size} selected
          {total > 0 ? ` · ${total} matching` : ""}
        </p>
        <label>
          Bulk status
          <select value={bulkChoice} onChange={(event) => setBulkChoice(event.target.value)}>
            <option value="">Choose a status</option>
            <option value="none">Not set</option>
            {statuses.map((status) => (
              <option key={status.id} value={status.id}>
                {status.label}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          disabled={busy || selected.size === 0 || bulkChoice === ""}
          onClick={() => void applyBulk()}
        >
          Set status for {selected.size} contacts
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy || total === 0}
          onClick={() => void selectMatching()}
        >
          Select all {total} matching
        </button>
        <button
          type="button"
          className="secondary"
          disabled={selected.size === 0}
          onClick={() => setSelected(new Set())}
        >
          Clear selection
        </button>
        <button
          type="button"
          className="secondary"
          disabled={busy || selected.size === 0}
          onClick={() => {
            setBusy(true);
            setError(null);
            downloadContacts([...selected])
              .catch((reason: unknown) => {
                setError(reason instanceof Error ? reason.message : "Could not download the CSV.");
              })
              .finally(() => setBusy(false));
          }}
        >
          Download selected CSV
        </button>
      </div>
      <div className="table-scroll">
        <table className="contacts-table">
          <thead>
            <tr>
              <th>
                <input
                  type="checkbox"
                  checked={allVisible}
                  ref={(node) => {
                    if (node) {
                      node.indeterminate = !allVisible && someVisible;
                    }
                  }}
                  onChange={togglePage}
                  aria-label="Select all contacts on this page"
                />
              </th>
              <th>Username</th>
              <th>Name</th>
              <th>Public email</th>
              <th>Kind</th>
              <th>LinkedIn</th>
              <th>Country</th>
              <th>Status</th>
              <th>Last checked</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9}>Loading contacts…</td>
              </tr>
            ) : contacts.length === 0 ? (
              <tr>
                <td colSpan={9}>No contacts match these filters.</td>
              </tr>
            ) : (
              contacts.map((contact) => (
                <tr key={contact.login}>
                  <td>
                    <input
                      type="checkbox"
                      checked={selected.has(contact.login)}
                      onChange={() => toggleOne(contact.login)}
                      aria-label={`Select ${contact.login}`}
                    />
                  </td>
                  <td>
                    <a href={contact.profile_url || `https://github.com/${contact.login}`}>
                      {contact.login}
                    </a>
                  </td>
                  <td>{contact.name || "—"}</td>
                  <td>{contact.public_email || "—"}</td>
                  <td>{contact.email_kinds.map(kindLabel).join(", ") || "—"}</td>
                  <td>
                    {contact.linkedin_url ? (
                      <a href={contact.linkedin_url}>Profile</a>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{contact.country || "—"}</td>
                  <td>
                    <select
                      aria-label={`Status for ${contact.login}`}
                      value={contact.outreach_status ?? "none"}
                      onChange={(event) => void changeRow(contact.login, event.target.value)}
                    >
                      <option value="none">Not set</option>
                      {contact.outreach_status && !labels.has(contact.outreach_status) ? (
                        <option value={contact.outreach_status}>{contact.outreach_status}</option>
                      ) : null}
                      {statuses.map((status) => (
                        <option key={status.id} value={status.id}>
                          {status.label}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td>{contact.last_checked_at ? contact.last_checked_at.slice(0, 16) : "—"}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <button
          type="button"
          className="secondary"
          disabled={(query.page || 1) <= 1}
          onClick={() => replace({ page: String((query.page || 1) - 1) }, false)}
        >
          Previous
        </button>
        <p className="muted">
          Page {query.page} of {pages}
        </p>
        <button
          type="button"
          className="secondary"
          disabled={(query.page || 1) >= pages}
          onClick={() => replace({ page: String((query.page || 1) + 1) }, false)}
        >
          Next
        </button>
        <label>
          Per page
          <select
            value={String(query.page_size || 25)}
            onChange={(event) => replace({ page_size: event.target.value }, true)}
          >
            {PAGE_SIZES.map((size) => (
              <option key={size} value={size}>
                {size}
              </option>
            ))}
          </select>
        </label>
      </div>
    </section>
  );
}
