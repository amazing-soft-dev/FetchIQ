"use client";

import { useEffect, useRef, useState } from "react";

import { checkMailbox, createCampaign } from "../lib/api";
import { isHtml, renderMessage, unknownPlaceholders } from "../lib/message";
import {
  ACTIVE_CAMPAIGN_STATUSES,
  useActiveCampaign,
  useCampaign,
  useCampaignAction,
  useSeedCampaign,
} from "../lib/queries";
import { messageDraftError } from "../lib/schemas";
import type { Campaign, Contact } from "../lib/types";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "./ui/dialog";
import { Field } from "./ui/field";
import { Input, Textarea } from "./ui/input";

type MailConnection =
  | { state: "unchecked" }
  | { state: "checking" }
  | { state: "connected"; from: string; host: string; port: number; sentFolder: string }
  | { state: "failed"; detail: string };

type Props = {
  open: boolean;
  selected: string[];
  contacts: Contact[];
  onClose: () => void;
  onDelivered: () => void;
};

function message(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}

export function MessageComposer({ open, selected, contacts, onClose, onDelivered }: Props) {
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [hourlyLimit, setHourlyLimit] = useState(30);
  const [delayMin, setDelayMin] = useState(30);
  const [delayMax, setDelayMax] = useState(60);
  // The send this dialog started or acted on, and whether the user dismissed the panel.
  const [ownCampaign, setOwnCampaign] = useState<Campaign | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [mailConnection, setMailConnection] = useState<MailConnection>({ state: "unchecked" });
  const delivered = useRef(0);
  const seenCampaign = useRef<string | null>(null);
  const notify = useRef(onDelivered);

  useEffect(() => {
    notify.current = onDelivered;
  }, [onDelivered]);

  // The send in progress when the page opened, unless the user dismissed it.
  const activeQuery = useActiveCampaign();
  const inherited = dismissed ? null : (activeQuery.data ?? null);
  const campaignId = ownCampaign?.id ?? inherited?.id ?? null;
  // Polled while queued or running; the latest poll wins over the copy we hold.
  const campaignQuery = useCampaign(campaignId, ownCampaign?.status ?? inherited?.status);
  const polled = campaignQuery.data?.id === campaignId ? campaignQuery.data : undefined;
  const campaign = campaignId === null ? null : (polled ?? ownCampaign ?? inherited);
  const campaignAction = useCampaignAction();
  const seedCampaign = useSeedCampaign();
  const loadFailure = activeQuery.error ?? campaignQuery.error;
  const shownError =
    error ?? (loadFailure ? message(loadFailure, "Could not load the message send.") : null);

  // Tell the contacts list when more messages have gone out since the last look.
  useEffect(() => {
    if (!campaign) {
      return;
    }
    if (seenCampaign.current !== campaign.id) {
      seenCampaign.current = campaign.id;
      delivered.current = campaign.counts.sent;
      return;
    }
    if (campaign.counts.sent > delivered.current) {
      delivered.current = campaign.counts.sent;
      notify.current();
    }
  }, [campaign]);

  const preview = selected
    .map((login) => contacts.find((contact) => contact.login === login))
    .find((contact): contact is Contact => Boolean(contact));
  const unknown = unknownPlaceholders(subject, body);
  const html = isHtml(body);
  const active = campaign !== null && ACTIVE_CAMPAIGN_STATUSES.includes(campaign.status);
  const loadedWithoutEmail = selected.filter((login) => {
    const contact = contacts.find((item) => item.login === login);
    return contact !== undefined && !contact.public_email;
  }).length;
  const draftProblem = messageDraftError({ subject, body, hourlyLimit, delayMin, delayMax });

  async function checkConnection() {
    setMailConnection({ state: "checking" });
    setError(null);
    try {
      const result = await checkMailbox();
      if (result.ok) {
        setMailConnection({
          state: "connected",
          from: result.from,
          host: result.host,
          port: result.port,
          sentFolder: result.sent_folder,
        });
        return;
      }
      setMailConnection({ state: "failed", detail: result.detail });
    } catch (reason: unknown) {
      setMailConnection({
        state: "failed",
        detail: message(reason, "Could not check the mailbox."),
      });
    }
  }

  async function send() {
    const problem = messageDraftError({ subject, body, hourlyLimit, delayMin, delayMax });
    if (problem) {
      setError(problem);
      return;
    }
    if (unknown.length > 0) {
      setError(`Unknown placeholder: ${unknown.map((name) => `{{ ${name} }}`).join(", ")}`);
      return;
    }
    if (mailConnection.state !== "connected") {
      setError("Check the mailbox before sending.");
      return;
    }
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
      seedCampaign(next);
      setOwnCampaign(next);
      setDismissed(false);
      onClose();
    } catch (reason: unknown) {
      setError(message(reason, "Could not start the message send."));
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
      setOwnCampaign(await campaignAction.mutateAsync({ campaignId: campaign.id, action }));
    } catch (reason: unknown) {
      setError(message(reason, "Could not update the message send."));
    } finally {
      setBusy(false);
    }
  }

  function dismiss() {
    setOwnCampaign(null);
    setDismissed(true);
  }

  const progress = campaign ? (
    <div className="rounded-control border border-line bg-header p-3">
      <p className="text-sm font-medium capitalize">{campaign.status}</p>
      <p className="mt-1 text-sm text-muted">
        {campaign.counts.sent} sent · {campaign.counts.failed} failed · {campaign.counts.queued}{" "}
        queued · {campaign.counts.skipped} skipped
      </p>
      {campaign.error ? <p className="mt-2 text-sm text-danger">{campaign.error}</p> : null}
      {campaign.failures.length > 0 ? (
        <ul className="mt-2 list-disc pl-5 text-sm text-danger">
          {campaign.failures.map((failure) => (
            <li key={failure.login}>
              {failure.login}: {failure.error || "Failed"}
            </li>
          ))}
        </ul>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {campaign.status === "running" || campaign.status === "queued" ? (
          <Button type="button" variant="secondary" disabled={busy} onClick={() => void act("pause")}>
            Pause
          </Button>
        ) : null}
        {campaign.status === "paused" ? (
          <Button type="button" disabled={busy} onClick={() => void act("resume")}>
            Resume
          </Button>
        ) : null}
        {active ? (
          <Button type="button" variant="secondary" disabled={busy} onClick={() => void act("cancel")}>
            Cancel
          </Button>
        ) : (
          <Button type="button" variant="secondary" onClick={dismiss}>
            Dismiss
          </Button>
        )}
      </div>
    </div>
  ) : null;

  if (!open && !campaign) {
    return null;
  }

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) {
            onClose();
          }
        }}
      >
        <DialogContent>
          <DialogTitle className="pr-8 text-lg font-semibold tracking-tight">Message</DialogTitle>
          <DialogDescription className="mt-1 text-sm text-muted">
            Use {"{{ first_name }}"}, {"{{ name }}"}, {"{{ login }}"}, or {"{{ email }}"}. The
            public email is the address the message is sent to. A message written in HTML is sent
            as HTML, with a plain-text copy for mail clients that do not show it.
          </DialogDescription>
          <div className="mt-4 flex flex-col gap-3">
            {shownError ? <p className="text-sm text-danger">{shownError}</p> : null}
            {open && !active ? (
              <>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    disabled={mailConnection.state === "checking"}
                    onClick={() => void checkConnection()}
                  >
                    Check connection
                  </Button>
                  <p className={mailConnection.state === "failed" ? "text-sm text-danger" : "text-sm text-muted"}>
                    {mailConnection.state === "unchecked" ? "Not checked" : null}
                    {mailConnection.state === "checking" ? "Checking mailbox..." : null}
                    {mailConnection.state === "connected"
                      ? `Connected: ${mailConnection.from} via ${mailConnection.host}:${mailConnection.port}. Copies go to ${mailConnection.sentFolder}.`
                      : null}
                    {mailConnection.state === "failed" ? mailConnection.detail : null}
                  </p>
                </div>
                <Field label="Subject">
                  <Input
                    type="text"
                    value={subject}
                    maxLength={200}
                    onChange={(event) => setSubject(event.target.value)}
                  />
                </Field>
                <Field label="Message">
                  <Textarea
                    value={body}
                    maxLength={20000}
                    placeholder="Hello {{ first_name }}"
                    onChange={(event) => setBody(event.target.value)}
                  />
                </Field>
                <div className="grid gap-3 sm:grid-cols-3">
                  <Field label="Maximum per hour">
                    <Input
                      type="number"
                      min={1}
                      max={500}
                      value={hourlyLimit}
                      onChange={(event) => setHourlyLimit(Number(event.target.value))}
                    />
                  </Field>
                  <Field label="Delay from (seconds)">
                    <Input
                      type="number"
                      min={0}
                      max={3600}
                      value={delayMin}
                      onChange={(event) => setDelayMin(Number(event.target.value))}
                    />
                  </Field>
                  <Field label="Delay to (seconds)">
                    <Input
                      type="number"
                      min={0}
                      max={3600}
                      value={delayMax}
                      onChange={(event) => setDelayMax(Number(event.target.value))}
                    />
                  </Field>
                </div>
                {unknown.length > 0 ? (
                  <p className="text-sm text-danger">
                    Unknown placeholder: {unknown.map((name) => `{{ ${name} }}`).join(", ")}
                  </p>
                ) : null}
                {preview ? (
                  <div className="rounded-control border border-line bg-header p-3 text-sm">
                    <p className="text-muted">
                      Preview for {preview.login}. To: {preview.public_email || "no public email"}
                      {html ? ". Sent as HTML." : null}
                    </p>
                    <p className="mt-2 font-medium">{renderMessage(subject, preview) || "Subject"}</p>
                    {html ? (
                      // An empty sandbox runs no script and cannot reach this page.
                      <iframe
                        title="Message preview"
                        sandbox=""
                        srcDoc={renderMessage(body, preview, { escapeHtml: true })}
                        className="mt-2 h-72 w-full rounded-control border border-line bg-white"
                      />
                    ) : (
                      <p className="mt-1 whitespace-pre-wrap">{renderMessage(body, preview) || "Message"}</p>
                    )}
                  </div>
                ) : (
                  <p className="text-sm text-muted">Select a contact on this page to preview the message.</p>
                )}
                {loadedWithoutEmail > 0 ? (
                  <p className="text-sm text-muted">
                    {loadedWithoutEmail} selected{" "}
                    {loadedWithoutEmail === 1 ? "contact has" : "contacts have"} no public email and
                    will be skipped.
                  </p>
                ) : null}
                <div className="flex flex-wrap gap-2">
                  <Button
                    type="button"
                    disabled={
                      busy ||
                      mailConnection.state !== "connected" ||
                      selected.length === 0 ||
                      draftProblem !== null ||
                      unknown.length > 0
                    }
                    onClick={() => void send()}
                  >
                    Send
                  </Button>
                  <Button type="button" variant="secondary" onClick={onClose}>
                    Close
                  </Button>
                </div>
              </>
            ) : null}
            {open ? progress : null}
          </div>
        </DialogContent>
      </Dialog>
      {!open && progress ? <div className="mt-4">{progress}</div> : null}
    </>
  );
}
