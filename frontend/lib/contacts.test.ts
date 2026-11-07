import { describe, expect, it } from "vitest";

import { parseSource, sourceLabels } from "./contacts";

describe("parseSource", () => {
  it("accepts the two sources and falls back to any", () => {
    expect(parseSource("github")).toBe("github");
    expect(parseSource("hackernews")).toBe("hackernews");
    expect(parseSource("any")).toBe("any");
    expect(parseSource("reddit")).toBe("any");
    expect(parseSource(null)).toBe("any");
    expect(parseSource(undefined)).toBe("any");
  });
});

describe("sourceLabels", () => {
  it("lists the record's own source first, then the others once", () => {
    expect(sourceLabels({ source: "github", sources: ["github"] })).toEqual(["GitHub"]);
    expect(sourceLabels({ source: "hackernews", sources: ["hackernews"] })).toEqual([
      "Hacker News",
    ]);
    expect(sourceLabels({ source: "github", sources: ["github", "hackernews"] })).toEqual([
      "GitHub",
      "Hacker News",
    ]);
    expect(sourceLabels({ source: "hackernews", sources: ["github", "hackernews"] })).toEqual([
      "Hacker News",
      "GitHub",
    ]);
  });

  it("includes the record's source even when the list lacks it", () => {
    expect(sourceLabels({ source: "github", sources: [] })).toEqual(["GitHub"]);
  });
});
