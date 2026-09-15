var __defProp = Object.defineProperty;
var __name = (target, value) => __defProp(target, "name", { value, configurable: true });

// src/index.ts
var index_default = {
  async email(message, env, _ctx) {
    const subject = message.headers.get("subject") ?? "";
    const from = message.from;
    const to = message.to;
    console.log(
      JSON.stringify({
        event: "inbound",
        from,
        to,
        subject,
        bytes: message.rawSize
      })
    );
    const raw = await new Response(message.raw).text();
    const phishingResult = detectPhishing(raw, subject, from);
    if (phishingResult.score >= env.PHISHING_REJECT_THRESHOLD) {
      console.warn(
        JSON.stringify({
          event: "phishing-rejected",
          from,
          to,
          subject,
          score: phishingResult.score,
          reasons: phishingResult.reasons
        })
      );
      message.setReject(`Phishing detected (score ${phishingResult.score}): ${phishingResult.reasons.join("; ")}`);
      return;
    }
    if (phishingResult.score >= 3) {
      console.warn(
        JSON.stringify({
          event: "phishing-warning",
          from,
          to,
          subject,
          score: phishingResult.score,
          reasons: phishingResult.reasons
        })
      );
    }
    if (!env.MAIL_INGEST_URL || !env.MAIL_INGEST_SECRET) {
      console.error("MAIL_INGEST_URL or MAIL_INGEST_SECRET unset");
      if (env.FALLBACK_FORWARD) {
        await message.forward(env.FALLBACK_FORWARD);
        return;
      }
      message.setReject("Mailbox ingest not configured");
      return;
    }
    try {
      const rawBytes = await new Response(message.raw).arrayBuffer();
      const res = await fetch(env.MAIL_INGEST_URL, {
        method: "POST",
        headers: {
          "content-type": "message/rfc822",
          "x-mail-ingest-secret": env.MAIL_INGEST_SECRET,
          "x-mail-to": to,
          "x-mail-from": from,
          "x-phishing-score": String(phishingResult.score),
          "x-phishing-reasons": phishingResult.reasons.join("|")
        },
        body: rawBytes
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => "");
        console.error(`ingest ${res.status}: ${detail.slice(0, 200)}`);
        if (env.FALLBACK_FORWARD) {
          await message.forward(env.FALLBACK_FORWARD);
          return;
        }
        message.setReject(`Ingest failed (${res.status})`);
        return;
      }
    } catch (err) {
      console.error("ingest threw", err);
      if (env.FALLBACK_FORWARD) {
        await message.forward(env.FALLBACK_FORWARD);
        return;
      }
      message.setReject("Ingest unavailable");
    }
  }
};
function detectPhishing(rawEmail, subject, from) {
  let score = 0;
  const reasons = [];
  const urlRegex = /https?:\/\/[^\s<>"']{4,}/gi;
  const urls = rawEmail.match(urlRegex) ?? [];
  const httpUrls = urls.filter((u) => u.startsWith("http://"));
  if (httpUrls.length > 0) {
    score += 4;
    reasons.push(`http_links:${httpUrls.length}`);
  }
  const redirectPatterns = [/\/redirect\//i, /\/r\.php/i, /\/goto\//i, /\/redir/i, /\/\?url=/i, /\/\?redirect=/i];
  const redirectUrls = urls.filter((u) => redirectPatterns.some((p) => p.test(u)));
  if (redirectUrls.length > 0) {
    score += 5;
    reasons.push(`redirect_urls:${redirectUrls.length}`);
  }
  const linkTextRegex = />([^<]{2,60})<\/a>/gi;
  const linkMatches = [...rawEmail.matchAll(linkTextRegex)];
  for (const m of linkMatches) {
    const text = m[1].toLowerCase();
    if (text.includes("webmail") || text.includes("access") || text.includes("click here") || text.includes("login")) {
      const hrefRegex = /href=["']([^"']+)["']/i;
      const surrounding = rawEmail.slice(Math.max(0, m.index - 200), m.index + m[0].length + 50);
      const hrefMatch = hrefRegex.exec(surrounding);
      if (hrefMatch) {
        const hrefDomain = extractDomain(hrefMatch[1]);
        const fromDomain2 = from.split("@")[1]?.toLowerCase() ?? "";
        if (hrefDomain && fromDomain2 && !hrefDomain.endsWith(fromDomain2) && !fromDomain2.endsWith(hrefDomain)) {
          score += 3;
          reasons.push(`domain_mismatch:${hrefDomain}`);
        }
      }
    }
  }
  const suspiciousTlds = [".xyz", ".top", ".click", ".loan", ".work", ".date", ".racing", ".stream", ".tk", ".ml", ".ga", ".cf"];
  const suspiciousUrls = urls.filter((u) => {
    const domain = extractDomain(u) ?? "";
    return suspiciousTlds.some((tld) => domain.endsWith(tld));
  });
  if (suspiciousUrls.length > 0) {
    score += 3;
    reasons.push(`suspicious_tld:${suspiciousUrls.length}`);
  }
  const urgencyKeywords = [
    "urgent",
    "verify your",
    "suspend",
    "deactivate",
    "confirm your",
    "update your",
    "validate",
    "action required",
    "archived in",
    "will be closed",
    "security alert",
    "unread items require"
  ];
  const subjectLower = subject.toLowerCase();
  const matchedUrgency = urgencyKeywords.filter((k) => subjectLower.includes(k));
  if (matchedUrgency.length > 0) {
    score += 2;
    reasons.push(`urgency_keywords:${matchedUrgency.join(",")}`);
  }
  const mailProviderDomains = [
    "google.com",
    "microsoft.com",
    "outlook.com",
    "yahoo.com",
    "protonmail.com",
    "cloudless.gr",
    "gmail.com",
    "icloud.com",
    "zoho.com",
    "fastmail.com"
  ];
  const fromDomain = from.split("@")[1]?.toLowerCase() ?? "";
  const isMailProvider = mailProviderDomains.some((d) => fromDomain.endsWith(d));
  const subjectHasMailKeywords = /webmail|mailbox|quarantine|encrypted message|secure.*message/i.test(subject);
  if (subjectHasMailKeywords && !isMailProvider) {
    score += 4;
    reasons.push(`fake_mail_notice_from:${fromDomain}`);
  }
  const ipUrls = urls.filter((u) => /https?:\/\/\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}/.test(u));
  if (ipUrls.length > 0) {
    score += 4;
    reasons.push(`ip_urls:${ipUrls.length}`);
  }
  return { score, reasons };
}
__name(detectPhishing, "detectPhishing");
function extractDomain(url) {
  try {
    const match = url.match(/https?:\/\/([^/]+)/);
    return match ? match[1].toLowerCase().replace(/^www\./, "") : null;
  } catch {
    return null;
  }
}
__name(extractDomain, "extractDomain");
export {
  index_default as default
};
//# sourceMappingURL=index.js.map
