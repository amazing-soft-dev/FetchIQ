import { describe, expect, it } from "vitest";

import { isHtml, placeholderValues, renderMessage, unknownPlaceholders } from "./message";

const contact = { name: "Ada Lovelace", login: "ada", public_email: "ada@example.com" };

describe("unknownPlaceholders", () => {
  it("returns nothing when every placeholder is known", () => {
    expect(unknownPlaceholders("Hi {{first_name}}", "{{ name }} {{login}} {{email}}")).toEqual([]);
  });

  it("reports each unknown placeholder once, across templates", () => {
    expect(
      unknownPlaceholders("Hi {{firstname}} {{company}}", "Re: {{company}} {{ role }}"),
    ).toEqual(["firstname", "company", "role"]);
  });

  it("ignores text that is not a placeholder", () => {
    expect(unknownPlaceholders("{first_name} {{ }} {{first-name}}")).toEqual([]);
  });
});

describe("placeholderValues", () => {
  it("splits the first name off the display name", () => {
    expect(placeholderValues(contact)).toEqual({
      first_name: "Ada",
      name: "Ada Lovelace",
      login: "ada",
      email: "ada@example.com",
    });
  });

  it("collapses extra whitespace in the name", () => {
    const values = placeholderValues({ ...contact, name: "  Ada   Lovelace " });
    expect(values.first_name).toBe("Ada");
    expect(values.name).toBe("Ada Lovelace");
  });

  it("falls back to the login when there is no name and to an empty email", () => {
    expect(placeholderValues({ name: null, login: "ada", public_email: null })).toEqual({
      first_name: "ada",
      name: "ada",
      login: "ada",
      email: "",
    });
    expect(placeholderValues({ ...contact, name: "   " }).first_name).toBe("ada");
  });
});

describe("renderMessage", () => {
  it("fills known placeholders, with or without inner spaces", () => {
    expect(renderMessage("Hi {{first_name}}, {{ name }} ({{login}}) <{{email}}>", contact)).toBe(
      "Hi Ada, Ada Lovelace (ada) <ada@example.com>",
    );
  });

  it("leaves unknown placeholders untouched", () => {
    expect(renderMessage("Hi {{first_name}} at {{company}}", contact)).toBe(
      "Hi Ada at {{company}}",
    );
  });

  it("escapes values for an HTML body only when asked", () => {
    const tagged = { ...contact, name: "Ada <b>& Co</b>" };
    expect(renderMessage("<p>{{ name }}</p>", tagged, { escapeHtml: true })).toBe(
      "<p>Ada &lt;b&gt;&amp; Co&lt;/b&gt;</p>",
    );
    expect(renderMessage("{{ name }}", tagged)).toBe("Ada <b>& Co</b>");
  });
});

describe("isHtml", () => {
  it.each([
    "<!DOCTYPE html><html><body>Hi</body></html>",
    "<p>Hello</p>",
    "Line one<br>Line two",
    "Line one<br />Line two",
    'See <a href="https://example.com">this</a>.',
    "<DIV>Hello</DIV>",
  ])("recognises markup: %s", (body) => {
    expect(isHtml(body)).toBe(true);
  });

  it.each([
    "Hello Ada",
    "Write to me <a@example.com> or <b@example.com>.",
    "{{ name }} <{{ email }}>",
    "3 < 4 and 5 > 2",
    "Use <placeholder> here.",
  ])("leaves plain text alone: %s", (body) => {
    expect(isHtml(body)).toBe(false);
  });
});
