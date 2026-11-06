import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("server-only", () => ({}));

async function load(token: string) {
  vi.stubEnv("API_TOKEN", token);
  vi.stubEnv("API_PROXY_TARGET", "http://api.test:8000/");
  vi.resetModules();
  return import("./server-api");
}

function stub(status: number, body: unknown): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(
    async () => new Response(JSON.stringify(body), { status }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("server API client", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("calls the backend directly with the token and no caching", async () => {
    const api = await load("abc");
    const fetchMock = stub(200, { countries: ["Germany"], us_states: [] });
    expect(await api.loadGeo()).toEqual({ countries: ["Germany"], us_states: [] });
    const [url, init] = (fetchMock.mock.calls as unknown as [string, RequestInit][])[0] ?? [];
    expect(url).toBe("http://api.test:8000/api/geo");
    expect((init?.headers as Record<string, string>).Authorization).toBe("Bearer abc");
    expect(init?.cache).toBe("no-store");
  });

  it("unwraps list envelopes and sends no header without a token", async () => {
    const api = await load("");
    const fetchMock = stub(200, { statuses: [{ id: "a", label: "A", position: 1 }] });
    expect(await api.loadOutreachStatuses()).toEqual([{ id: "a", label: "A", position: 1 }]);
    const [, init] = (fetchMock.mock.calls as unknown as [string, RequestInit][])[0] ?? [];
    expect(init?.headers).not.toHaveProperty("Authorization");
  });

  it("tells a missing job apart from an unavailable backend", async () => {
    const api = await load("");
    stub(404, { detail: "job not found" });
    expect(await api.loadJob("nope")).toEqual({ missing: true });
    stub(503, { detail: "down" });
    expect(await api.loadJob("j1")).toEqual({ unavailable: true });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("fetch failed");
      }),
    );
    expect(await api.loadPresets()).toBeUndefined();
  });
});
