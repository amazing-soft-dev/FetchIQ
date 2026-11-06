import { describe, expect, it } from "vitest";

import { messageDraftError, searchCriteriaError } from "./schemas";
import type { SearchCriteria } from "./types";

const criteria: SearchCriteria = {
  countries: ["US"],
  us_states: [],
  keywords: [],
  account_age: { min_years: 0, min_months: 0, max_years: 5, max_months: 0 },
  candidate_limit: 100,
};

function withAge(age: Partial<SearchCriteria["account_age"]>): SearchCriteria {
  return { ...criteria, account_age: { ...criteria.account_age, ...age } };
}

describe("searchCriteriaError", () => {
  it("accepts a complete search", () => {
    expect(searchCriteriaError(criteria)).toBeNull();
  });

  it("requires at least one country", () => {
    expect(searchCriteriaError({ ...criteria, countries: [] })).toBe(
      "Select at least one country.",
    );
  });

  it("rejects an age that is not whole months", () => {
    expect(searchCriteriaError(withAge({ min_months: 1.5 }))).toBe(
      "Account age must be a whole number of years and months.",
    );
    expect(searchCriteriaError(withAge({ max_years: -1 }))).toBe(
      "Account age must be a whole number of years and months.",
    );
  });

  it("rejects months above 11 and years above 100", () => {
    expect(searchCriteriaError(withAge({ min_months: 12 }))).toBe(
      "Years must be 0 to 100 and months must be 0 to 11.",
    );
    expect(searchCriteriaError(withAge({ max_years: 101 }))).toBe(
      "Years must be 0 to 100 and months must be 0 to 11.",
    );
  });

  it("rejects a minimum age above the maximum", () => {
    expect(searchCriteriaError(withAge({ min_years: 5, min_months: 1 }))).toBe(
      "Minimum account age must be less than or equal to the maximum.",
    );
    expect(searchCriteriaError(withAge({ min_years: 5, max_months: 0 }))).toBeNull();
  });

  it("bounds the candidate limit", () => {
    const message = "Candidate limit must be between 1 and 5000.";
    expect(searchCriteriaError({ ...criteria, candidate_limit: 0 })).toBe(message);
    expect(searchCriteriaError({ ...criteria, candidate_limit: 5001 })).toBe(message);
    expect(searchCriteriaError({ ...criteria, candidate_limit: 2.5 })).toBe(message);
    expect(searchCriteriaError({ ...criteria, candidate_limit: 5000 })).toBeNull();
  });
});

const draft = {
  subject: "Hello {{first_name}}",
  body: "Saw your work on GitHub.",
  hourlyLimit: 20,
  delayMin: 30,
  delayMax: 90,
};

describe("messageDraftError", () => {
  it("accepts a complete draft", () => {
    expect(messageDraftError(draft)).toBeNull();
  });

  it("requires a subject and a body, ignoring whitespace", () => {
    expect(messageDraftError({ ...draft, subject: "   " })).toBe("Subject is required.");
    expect(messageDraftError({ ...draft, body: "\n\t" })).toBe("Message is required.");
  });

  it("rejects a subject or body that is too long", () => {
    expect(messageDraftError({ ...draft, subject: "x".repeat(201) })).not.toBeNull();
    expect(messageDraftError({ ...draft, body: "x".repeat(20001) })).not.toBeNull();
    expect(messageDraftError({ ...draft, subject: "x".repeat(200) })).toBeNull();
  });

  it("bounds the hourly limit", () => {
    expect(messageDraftError({ ...draft, hourlyLimit: 1.5 })).toBe(
      "Maximum per hour must be a whole number.",
    );
    expect(messageDraftError({ ...draft, hourlyLimit: 0 })).toBe(
      "Maximum per hour must be at least 1.",
    );
    expect(messageDraftError({ ...draft, hourlyLimit: 501 })).toBe(
      "Maximum per hour must be 500 or less.",
    );
  });

  it("bounds each delay", () => {
    expect(messageDraftError({ ...draft, delayMin: 0.5 })).toBe(
      "Delay must be a whole number of seconds.",
    );
    expect(messageDraftError({ ...draft, delayMin: -1 })).toBe("Delay must be 0 or more.");
    expect(messageDraftError({ ...draft, delayMax: 3601 })).toBe(
      "Delay must be 3600 seconds or less.",
    );
  });

  it("requires the delay range to be ordered", () => {
    expect(messageDraftError({ ...draft, delayMin: 91 })).toBe(
      "Delay from must be less than or equal to delay to.",
    );
    expect(messageDraftError({ ...draft, delayMin: 90 })).toBeNull();
  });
});
