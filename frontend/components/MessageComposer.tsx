"use client";

import { useEffect, useRef, useState } from "react";

import {
  createCampaign,
  getActiveCampaign,
  getCampaign,
  postCampaignAction,
} from "../lib/api";
import { renderMessage, unknownPlaceholders } from "../lib/message";
import type { Campaign, Contact } from "../lib/types";

const ACTIVE = new Set(["queued", "running", "paused"]);

type Props = {
  open: boolean;
  selected: string[];
  contacts: Contact[];
  onClose: () => void;
  onDelivered: () => void;
};

export function MessageComposer({ open, selected, contacts, onClose, onDelivered }: Props) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [hourlyLimit, setHourlyLimit] = useState(30);
  const [delayMin, setDelayMin] = useState(30);
  const [delayMax, setDelayMax] = useState(60);
  const [campaign, setCampaign] = useState<Campaign | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const delivered = useRef(0);
  const notify = useRef(onDelivered);

  useEffect(() => {
    notify.current = onDelivered;
  }, [onDelivered]);

  const preview = selected
    .map((login) => contacts.find((contact) => contact.login === login))
    .find((contact): contact is Contact => Boolean(contact));
  const unknown = unknownPlaceholders(subject, body);
  const active = campaign !== null && ACTIVE.has(campaign.status);
  const loadedWithoutEmail = selected.filter((login) => {
    const contact = contacts.find((item) => item.login === login);
    return contact !== undefined && !contact.public_email;
  }).length;

  useEffect(() => {
    let cancelled = false;
    getActiveCampaign()
      .then((next) => {
        if (!cancelled && next) {
          delivered.current = next.counts.sent;
          setCampaign(next);
        }
      })
      .catch((reason: unknown) => {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Could not load the message send.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const campaignId = campaign?.id ?? null;
  const campaignStatus = campaign?.status ?? null;

  useEffect(() => {
    if (
      !campaignId ||
      !campaignStatus ||
      !ACTIVE.has(campaignStatus) ||
      campaignStatus === "paused"
    ) {
      return;
    }
    const id = campaignId;
    let cancelled = false;

    async function refresh() {
      try {
        const next = await getCampaign(id);
        if (cancelled) {
          return;
        }
        setCampaign(next);
        if (next.counts.sent > delivered.current) {
          delivered.current = next.counts.sent;
          notify.current();
        }
      } catch (reason: unknown) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : "Could not load the message send.");
        }
      }
    }

    const timer = window.setInterval(() => {
      void refresh();
    }, 2000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [campaignId, campaignStatus]);

  async function send() {
    setBusy(true);
    setError(null);
    try {
      const next = await createCampaign({
        logins: selected,
        subject: subject.trim(),
        body: body.trim(),
        hourly_limit: hourlyLimit,
        delay_min_seconds: delayMin,
        delay_max_seconds: delayMax,
      });
      delivered.current = next.counts.sent;
      setCampaign(next);
      onClose();
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Could not start the message send.");
    } finally {
      setBusy(false);
    }
  }

  async function act(action: "pause" | "resume" | "cancel") {
    if (!campaign) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      setCampaign(await postCampaignAction(campaign.id, action));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "Could not update the message send.");
    } finally {
      setBusy(false);
    }
  }

  if (!open && !campaign) {
    return null;
  }

  return (
    <section className="message-panel">
      {error ? <p className="error">{error}</p> : null}
      {open && !active ? (
        <>
          <label>
            Subject
            <input
              type="text"
              value={subject}
              maxLength={200}
              onChange={(event) => setSubject(event.target.value)}
            />
          </label>
          <label>
            Message
            <textarea
              value={body}
              maxLength={20000}
              placeholder="Hello {{ first_name }}"
              onChange={(event) => setBody(event.target.value)}
            />
          </label>
          <div className="message-limits">
            <label>
              Maximum per hour
              <input
                type="number"
                min={1}
                max={500}
                value={hourlyLimit}
                onChange={(event) => setHourlyLimit(Number(event.target.value))}
              />
            </label>
            <label>
              Delay from (seconds)
              <input
                type="number"
                min={0}
                max={3600}
                value={delayMin}
                onChange={(event) => setDelayMin(Number(event.target.value))}
              />
            </label>
            <label>
              Delay to (seconds)
              <input
                type="number"
                min={0}
                max={3600}
                value={delayMax}
                onChange={(event) => setDelayMax(Number(event.target.value))}
              />
            </label>
          </div>
          <p className="muted">
            Use {"{{ first_name }}"}, {"{{ name }}"}, {"{{ login }}"}, or {"{{ email }}"}. The
            public email is the address the message is sent to.
          </p>
          {unknown.length > 0 ? (
            <p className="error">
              Unknown placeholder: {unknown.map((name) => `{{ ${name} }}`).join(", ")}
            </p>
          ) : null}
          {preview ? (
            <div className="message-preview">
              <p>
                Preview for {preview.login}. To: {preview.public_email || "no public email"}
              </p>
              <p>{renderMessage(subject, preview) || "Subject"}</p>
              <p>{renderMessage(body, preview) || "Message"}</p>
            </div>
          ) : (
            <p className="muted">Select a contact on this page to preview the message.</p>
          )}
          {loadedWithoutEmail > 0 ? (
            <p className="muted">
              {loadedWithoutEmail} selected {loadedWithoutEmail === 1 ? "contact has" : "contacts have"}{" "}
              no public email and will be skipped.
            </p>
          ) : null}
          <div className="filter-actions">
            <button
              type="button"
              disabled={
                busy ||
                selected.length === 0 ||
                !subject.trim() ||
                !body.trim() ||
                unknown.length > 0 ||
                delayMin > delayMax ||
                hourlyLimit < 1
              }
              onClick={() => void send()}
            >
              Send
            </button>
            <button type="button" className="secondary" onClick={onClose}>
              Close
            </button>
          </div>
        </>
      ) : null}
      {campaign ? (
        <div className="message-progress">
          <p className={`status status-${campaign.status}`}>{campaign.status}</p>
          <p className="muted">
            {campaign.counts.sent} sent · {campaign.counts.failed} failed · {campaign.counts.queued}{" "}
            queued · {campaign.counts.skipped} skipped
          </p>
          {campaign.error ? <p className="error">{campaign.error}</p> : null}
          {campaign.failures.length > 0 ? (
            <ul className="message-failures">
              {campaign.failures.map((failure) => (
                <li key={failure.login}>
                  {failure.login}: {failure.error || "Failed"}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="filter-actions">
            {campaign.status === "running" || campaign.status === "queued" ? (
              <button type="button" className="secondary" disabled={busy} onClick={() => void act("pause")}>
                Pause
              </button>
            ) : null}
            {campaign.status === "paused" ? (
              <button type="button" disabled={busy} onClick={() => void act("resume")}>
                Resume
              </button>
            ) : null}
            {active ? (
              <button type="button" className="secondary" disabled={busy} onClick={() => void act("cancel")}>
                Cancel
              </button>
            ) : (
              <button type="button" className="secondary" onClick={() => setCampaign(null)}>
                Dismiss
              </button>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}
