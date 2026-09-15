import { describe, expect, it } from "vitest";

import { detectPhishing, parseRejectThreshold } from "./index";

describe("detectPhishing", () => {
  it("rejects the observed fake webmail notification", () => {
    const raw = `<a href="http://utahlemonlaw.org/redirect/index.php?email=user@cloudless.gr">ACCESS WEBMAIL</a>`;
    const result = detectPhishing(raw, "Secure webmail notification", "notice@example.net");

    expect(result.score).toBeGreaterThanOrEqual(8);
    expect(result.reasons).toContain("http_links:1");
    expect(result.reasons).toContain("redirect_urls:1");
    expect(result.reasons).toContain("domain_mismatch:utahlemonlaw.org");
  });

  it("allows a normal HTTPS message", () => {
    const raw = `<a href="https://cloudless.gr/blog">Read the Cloudless blog</a>`;
    const result = detectPhishing(raw, "Weekly update", "news@cloudless.gr");

    expect(result).toEqual({ score: 0, reasons: [] });
  });
});

describe("parseRejectThreshold", () => {
  it.each([
    ["8", 8],
    [12, 12],
    [undefined, 8],
    ["invalid", 8],
    ["0", 8],
  ])("parses %s as %s", (value, expected) => {
    expect(parseRejectThreshold(value)).toBe(expected);
  });
});
