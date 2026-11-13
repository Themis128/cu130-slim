// followup.js — send the supplementary report (followup.txt) to the
// Ombudsman via the stack's SMTP, then Telegram-notify the result.
// Triggered by monitor.js when the complaint has had no news for 3+ days.
// Usage: node followup.js [--dry-run]
const fs = require('fs');
const path = require('path');
const tls = require('tls');
const net = require('net');
const https = require('https');

const DIR = __dirname;
const CFG = JSON.parse(fs.readFileSync(path.join(DIR, 'config.json'), 'utf8'));
const DRY = process.argv.includes('--dry-run');
const TEST = process.argv.includes('--test');      // send self-test to own mailbox via full chain
const TEST_SMTP = process.argv.includes('--test-smtp'); // force SMTP leg only
const TO = TEST ? 'tbaltzakis@cloudless.gr' : 'press@synigoros.gr';
const ALERT_STATE = path.join(DIR, 'followup-alert.json');
const FAIL_ALERT_EVERY_MS = 60 * 60 * 1000; // one failure alert per hour max

function envVal(key) {
  try {
    const env = fs.readFileSync(path.join(DIR, '..', '..', '.env'), 'utf8');
    const m = env.match(new RegExp('^' + key + '=(.+)$', 'm'));
    return m && m[1].trim().replace(/^["']|["']$/g, '');
  } catch { return null; }
}

function tg(text) {
  return new Promise((resolve) => {
    const tok = process.env.TELEGRAM_BOT_TOKEN || envVal('TELEGRAM_BOT_TOKEN');
    if (!tok) return resolve(false);
    const body = JSON.stringify({ chat_id: CFG.chatId, text });
    const req = https.request({ hostname: 'api.telegram.org', port: 443, path: '/bot' + tok + '/sendMessage',
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } },
      (res) => { res.resume(); res.on('end', () => resolve(res.statusCode === 200)); });
    req.on('error', () => resolve(false));
    req.end(body);
  });
}

// Minimal SMTP client — 465 direct TLS, or 587 STARTTLS upgrade (Resend).
function smtpSend(host, port, user, pass, from, to, subject, text) {
  return new Promise((resolve, reject) => {
    const b64 = (s) => Buffer.from(s).toString('base64');
    const steps = [`EHLO monitor`, `AUTH LOGIN`, b64(user), b64(pass),
      `MAIL FROM:<${from}>`, `RCPT TO:<${to}>`, `DATA`,
      `From: ${from}\r\nTo: ${to}\r\nSubject: =?UTF-8?B?${b64(subject)}?=\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n${text}\r\n.`,
      `QUIT`];
    let buf = '', i = 0, sock;
    const onData = (d) => {
      buf += d;
      if (!/(^|\r\n)\d{3} /.test(buf)) return;
      const code = parseInt(buf.slice(0, 3), 10);
      const txt = buf; buf = '';
      if (code >= 400) { sock.destroy(); return reject(new Error('SMTP ' + code + ' ' + txt.slice(4, 80))); }
      if (i < steps.length) sock.write(steps[i++] + '\r\n');
      else { sock.end(); resolve(true); }
    };
    const fail = (e) => reject(e instanceof Error ? e : new Error(String(e)));
    // Node rejects servername set to an IP literal (ERR_INVALID_ARG_VALUE)
    const sni = net.isIP(host) ? undefined : host;
    if (port === 465) {
      sock = tls.connect(port, host, { servername: sni, rejectUnauthorized: envVal('SMTP_SSL_VERIFY') !== 'false' });
      sock.on('data', onData);
      sock.on('error', fail);
    } else {
      // plain connect → EHLO → STARTTLS → upgrade → EHLO → auth flow
      const net = require('net');
      sock = net.connect(port, host);
      let phase = 0;
      sock.on('data', (d) => {
        buf += d;
        if (!/(^|\r\n)\d{3} /.test(buf)) return;
        const code = parseInt(buf.slice(0, 3), 10); const txt = buf; buf = '';
        if (code >= 400) { sock.destroy(); return fail(new Error('SMTP ' + code)); }
        if (phase === 0) { phase = 1; sock.write('EHLO monitor\r\n'); }
        else if (phase === 1) {
          if (!/STARTTLS/i.test(txt)) return fail(new Error('no STARTTLS support'));
          phase = 2; sock.write('STARTTLS\r\n');
        } else if (phase === 2) {
          // 220 → upgrade socket to TLS and resume the normal flow
          const raw = sock;
          raw.removeAllListeners('data');
          sock = tls.connect({ socket: raw, servername: sni, rejectUnauthorized: envVal('SMTP_SSL_VERIFY') !== 'false' }, () => {
            sock.on('data', onData);
            sock.on('error', fail);
            sock.write('EHLO monitor\r\n');
          });
          sock.on('error', fail);
        }
      });
      sock.on('error', fail);
    }
    sock.setTimeout(30000, () => { sock.destroy(); fail(new Error('SMTP timeout')); });
  });
}

// HTTP fallback via the mail-outbound Worker (Cloudflare send_email
// binding) — used when SMTP auth is unavailable. Requires
// MAIL_OUTBOUND_URL + MAIL_OUTBOUND_TOKEN in .env.
function httpSend(url, token, from, to, subject, text) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({
      to, subject, text, from,
      category: 'ops-alert',
    });
    const u = new URL(url.replace(/\/$/, ''));
    if (!u.pathname.endsWith('/send')) u.pathname = u.pathname.replace(/\/$/, '') + '/send';
    const req = https.request({
      hostname: u.hostname, port: 443, path: u.pathname,
      method: 'POST',
      headers: { 'Content-Type': 'application/json',
        'Authorization': 'Bearer ' + token,
        'User-Agent': 'synigoros-monitor/1.0',
        'Content-Length': Buffer.byteLength(body) },
    }, (res) => {
      let d = '';
      res.on('data', (c) => { d += c; });
      res.on('end', () => {
        if (res.statusCode === 200) resolve(true);
        else reject(new Error('mail-outbound HTTP ' + res.statusCode + ' ' + d.slice(0, 120)));
      });
    });
    req.on('error', reject);
    req.setTimeout(30000, () => { req.destroy(new Error('mail-outbound timeout')); });
    req.end(body);
  });
}

(async () => {
  const raw = fs.readFileSync(path.join(DIR, 'followup.txt'), 'utf8');
  let subject = raw.match(/^Subject:\s*(.+)$/m)[1].trim();
  let body = raw.replace(/^Subject:.*\r?\n\s*\r?\n/, '');
  if (TEST) { subject = '[TEST] synigoros followup path check'; body = 'Self-test via ' + (TEST_SMTP ? 'SMTP-only' : 'full chain') + ' — ' + new Date().toISOString(); }
  // Primary SMTP = omv-ha postfix relay (SMTP_HOST/SMTP_PORT, mailbox
  // account auth — verified 2026-11-13). smtp.resend.com fallback only
  // applies when SMTP_HOST is unset AND the creds are Resend-style
  // (user "resend" + API key); the mail-outbound Worker can only reach
  // verified CF destination addresses, never external recipients.
  const host = envVal('SMTP_HOST') || envVal('SMTP_RELAY_HOST') || 'smtp.resend.com';
  const port = parseInt(envVal('SMTP_PORT') || envVal('SMTP_RELAY_PORT') || '587', 10);
  const user = envVal('SMTP_USER'), pass = envVal('SMTP_PASSWORD'), from = envVal('SMTP_FROM');
  const outUrl = envVal('MAIL_OUTBOUND_URL'), outTok = envVal('MAIL_OUTBOUND_TOKEN');
  if (DRY) { console.log('DRY-RUN to', TO, 'via', host + ':' + port, 'from', from, '\nSUBJ:', subject, '\nBODY:', body.slice(0, 200)); return; }
  try {
    if (outUrl && outTok && !TEST_SMTP) {
      try {
        await httpSend(outUrl, outTok, from || 'no-reply@cloudless.gr', TO, subject, body);
      } catch (e) {
        console.log('mail-outbound failed, falling back to SMTP:', e.message);
        await smtpSend(host, port, user, pass, from, TO, subject, body);
      }
    } else {
      await smtpSend(host, port, user, pass, from, TO, subject, body);
    }
    console.log('followup sent to', TO);
    await tg((TEST ? '🧪 TEST αποστολή follow-up (' + (TEST_SMTP ? 'SMTP-only' : 'full chain') + ') -> ' + TO : '📮 Follow-up εστάλη στον Συνήγορο (email ' + TO + ', αρ. πρωτ. 408302/81750/2026)'));
  } catch (e) {
    console.log('followup send failed:', e.message);
    // The monitor retries every ~15min while SMTP is down — without
    // throttling that spammed Telegram once per retry. Alert at most
    // once per hour; the retry itself keeps running regardless.
    let lastAlert = 0;
    try { lastAlert = JSON.parse(fs.readFileSync(ALERT_STATE, 'utf8')).ts || 0; } catch {}
    if (Date.now() - lastAlert >= FAIL_ALERT_EVERY_MS) {
      fs.writeFileSync(ALERT_STATE, JSON.stringify({ ts: Date.now() }));
      await tg('⚠️ ΑΠΟΤΥΧΙΑ αποστολής follow-up Συνηγόρου: ' + e.message + ' — στείλε χειροκίνητα (scripts/synigoros-monitor/followup.txt)');
    }
    process.exit(1);
  }
})();
