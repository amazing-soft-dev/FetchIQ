import { afterEach, describe, expect, it, vi } from "vitest";

import { errorMessage } from "./api";

function response(body: string, status = 400): Response {
  return new Response(body, { status, headers: { "Content-Type": "application/json" } });
}

describe("errorMessage", () => {
  it("returns a string detail as is", async () => {
    expect(await errorMessage(response(JSON.stringify({ detail: "Job not found" }), 404))).toBe(
      "Job not found",
    );
  });

  it("joins the messages of a validation error list", async () => {
    const body = {
      detail: [
        { loc: ["body", "criteria"], msg: "Select at least one country." },
        { msg: "Candidate limit must be between 1 and 5000." },
        { loc: ["body"] },
        null,
      ],
    };
    expect(await errorMessage(response(JSON.stringify(body), 422))).toBe(
      "Select at least one country. Candidate limit must be between 1 and 5000.",
    );
  });

  it("falls back to the status for a non-JSON body", async () => {
    expect(await errorMessage(response("<html>Bad Gateway</html>", 502))).toBe(
      "Request failed (502)",
    );
  });

  it("falls back to the status when detail has another shape", async () => {
    expect(await errorMessage(response(JSON.stringify({ detail: { code: 1 } }), 500))).toBe(
      "Request failed (500)",
    );
    expect(await errorMessage(response(JSON.stringify({ error: "x" }), 403))).toBe(
      "Request failed (403)",
    );
  });
});

async function loadApi(token: string) {
  vi.stubEnv("NEXT_PUBLIC_API_TOKEN", token);
  vi.resetModules();
  return import("./api");
}

function stubFetch(body: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(
    async () => new Response(JSON.stringify(body), { status: 200 }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("API token", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("sends a bearer header and a token query on export links when set", async () => {
    const api = await loadApi(" abc ");
    const fetchMock = stubFetch({ jobs: [] });
    await api.listJobs();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer abc");
    expect(api.exportHref("j1", true)).toBe("/api/jobs/j1/export.csv?include_no_contact=true&token=abc");
  });

  it("sends nothing extra when the token is empty", async () => {
    const api = await loadApi("");
    const fetchMock = stubFetch({ jobs: [] });
    await api.listJobs();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.headers).not.toHaveProperty("Authorization");
    expect(api.exportHref("j1", false)).toBe("/api/jobs/j1/export.csv?include_no_contact=false");
  });
});
