import { describe, expect, it } from "vitest";

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
