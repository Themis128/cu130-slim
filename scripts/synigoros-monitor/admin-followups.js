// admin-followups.js — dated follow-up scheduler for the ΔΥΠΑ / ΣΕΠΕ /
// Ombudsman case files. Runs inside the SynigorosMonitor scheduled task
// (spawned by monitor.js every ~15 min); sends each event in
// followups.json once, on/after its sendAt date, then Telegram-notifies.
// Usage: node admin-followups.js [--dry-run] [--list]
const fs = require('fs');
const path = require('path');
const tls = require('tls');
const net = require('net');
const https = require('https');
const { execFileSync, execFile } = require('child_process');

const DIR = __dirname;
const CFG = JSON.parse(fs.readFileSync(path.join(DIR, 'config.json'), 'utf8'));
const SCHEDULE = JSON.parse(fs.readFileSync(path.join(DIR, 'followups.json'), 'utf8'));
const STATE_FILE = path.join(DIR, 'admin-followups-state.json');
const MONITOR_STATE = path.join(DIR, 'state.json');
const DRY = process.argv.includes('--dry-run');
const LIST = process.argv.includes('--list');

// Conditional-skip helpers: an event may declare
//   "requireStatus": "Προς Αποδοχή"   — skip if monitor state.json status differs
//   "skipIfRepliedFrom": "domain" + "skipIfRepliedSince": "YYYY-MM-DD"
//                       — skip if the cloudless.gr mailbox got mail from that
//                         domain since the date (IMAP check via check-replies.py)
function pythonBin() {
  for (const c of [process.env.PYTHON, 'python', 'py',
                   'C:\\Program Files\\Python312\\python.exe']) {
    if (!c) continue;
    try { execFileSync(c, ['--version'], { stdio: 'ignore', timeout: 5000 }); return c; }
    catch {}
  }
  return 'python';
}

function checkReplied(sender, since, requireRe) {
  const args = [path.join(DIR, 'check-replies.py'), '--from', sender, '--since', since];
  if (requireRe) args.push('--require', requireRe);
  try {
    const out = execFileSync(pythonBin(), args, { timeout: 90000 }).toString();
    return JSON.parse(out);
  } catch (e) {
    return { replied: false, error: 'check-replies failed: ' + e.message };
  }
}

function monitorStatus() {
  try { return JSON.parse(fs.readFileSync(MONITOR_STATE, 'utf8')).status || null; }
  catch { return null; }
}

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

// Minimal SMTP client — multi-recipient, 465 direct TLS or 587 STARTTLS.
function smtpSend(host, port, user, pass, from, toList, subject, text) {
  return new Promise((resolve, reject) => {
    const b64 = (s) => Buffer.from(s).toString('base64');
    const rcpts = toList.map((t) => `RCPT TO:<${t}>`);
    const steps = [`EHLO monitor`, `AUTH LOGIN`, b64(user), b64(pass),
      `MAIL FROM:<${from}>`, ...rcpts, `DATA`,
      `From: ${from}\r\nTo: ${toList.join(', ')}\r\nSubject: =?UTF-8?B?${b64(subject)}?=\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n${text}\r\n.`,
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

// HTTP fallback via the mail-outbound Worker — primary path when configured.
function httpSend(url, token, from, to, subject, text) {
  return new Promise((resolve, reject) => {
    const body = JSON.stringify({ to, subject, text, from, category: 'ops-alert' });
    const u = new URL(url.replace(/\/$/, ''));
    if (!u.pathname.endsWith('/send')) u.pathname = u.pathname.replace(/\/$/, '') + '/send';
    const req = https.request({
      hostname: u.hostname, port: 443, path: u.pathname, method: 'POST',
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

function loadState() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); } catch { return { sent: {} }; }
}

(async () => {
  const state = loadState();
  state.sent = state.sent || {};
  const now = Date.now();

  if (LIST) {
    for (const ev of SCHEDULE.events) {
      const due = now >= new Date(ev.sendAt + 'T08:00:00+03:00').getTime();
      console.log(`${ev.id}  sendAt=${ev.sendAt}  ${due ? 'DUE' : 'pending'}  sent=${!!state.sent[ev.id]}  ${ev.tgOnly ? 'TG: ' + ev.tgText : '-> ' + ev.to.join(',') + '  file=' + ev.file}`);
    }
    return;
  }

  // Inbound reply watcher — same ~15min cadence via monitor.js spawn.
  // Fire-and-forget; reply-watch.py self-throttles via reply-watch-state.json.
  try {
    execFile(pythonBin(), [path.join(DIR, 'reply-watch.py')],
      { stdio: 'ignore', detached: true }).unref();
  } catch (e) { console.log('reply-watch spawn failed: ' + e.message); }

  let changed = false;
  for (const ev of SCHEDULE.events) {
    if (state.sent[ev.id]) continue;
    const dueAt = new Date(ev.sendAt + 'T08:00:00+03:00').getTime();
    if (now < dueAt) continue;

    // Gate: skip when the monitored status no longer matches (applies
    // to tgOnly reminders too — e.g. no call reminder once accepted).
    if (ev.requireStatus) {
      const cur = monitorStatus();
      if (cur !== ev.requireStatus) {
        const why = 'skipped: status=' + cur + ' (required ' + ev.requireStatus + ')';
        if (DRY) { console.log('DRY-SKIP ' + ev.id + ': ' + why); continue; }
        state.sent[ev.id] = why;
        changed = true;
        console.log(ev.id + ' ' + why);
        await tg(`⏭️ ${ev.id} παραλείφθηκε — ${why}`);
        continue;
      }
    }

    // tg-only reminder events (e.g. scheduled phone calls) — no email sent.
    if (ev.tgOnly) {
      if (DRY) { console.log(`DRY-TG ${ev.id}: ${ev.tgText}`); continue; }
      const ok = await tg(`⏰ ${ev.tgText}`);
      if (ok) {
        state.sent[ev.id] = new Date().toISOString();
        changed = true;
        console.log(ev.id + ' tg reminder sent');
      } else {
        console.log(ev.id + ' tg reminder failed');
      }
      continue;
    }

    const raw = fs.readFileSync(path.join(DIR, ev.file), 'utf8');
    const subject = raw.match(/^Subject:\s*(.+)$/m)[1].trim();
    const body = raw.replace(/^Subject:.*\r?\n\s*\r?\n/, '');

    if (DRY) {
      console.log(`DRY ${ev.id} -> ${ev.to.join(', ')} | ${subject}`);
      continue;
    }

    // Gate: skip the email if the authority already replied by mail.
    if (ev.skipIfRepliedFrom) {
      const r = checkReplied(ev.skipIfRepliedFrom, ev.skipIfRepliedSince || ev.sendAt,
        ev.skipIfRepliedRequire);
      if (r.replied) {
        const why = 'skipped: reply from ' + ev.skipIfRepliedFrom +
          ' since ' + (ev.skipIfRepliedSince || ev.sendAt);
        state.sent[ev.id] = why;
        changed = true;
        console.log(ev.id + ' ' + why);
        await tg(`⏭️ ${ev.id} παραλείφθηκε — ${why} (${(r.matches[0] || {}).subject || ''})`);
        continue;
      }
      if (r.error) console.log(ev.id + ': reply-check failed (sending anyway): ' + r.error);
    }

    // Primary SMTP = omv-ha postfix relay (SMTP_HOST/SMTP_PORT). The
    // mail-outbound Worker only reaches verified CF destination addresses,
    // so external recipients always fall through to SMTP anyway.
    const host = envVal('SMTP_HOST') || envVal('SMTP_RELAY_HOST') || 'smtp.resend.com';
    const port = parseInt(envVal('SMTP_PORT') || envVal('SMTP_RELAY_PORT') || '587', 10);
    const user = envVal('SMTP_USER'), pass = envVal('SMTP_PASSWORD'), from = envVal('SMTP_FROM');
    const outUrl = envVal('MAIL_OUTBOUND_URL'), outTok = envVal('MAIL_OUTBOUND_TOKEN');
    try {
      if (outUrl && outTok) {
        try {
          await httpSend(outUrl, outTok, from || 'no-reply@cloudless.gr', ev.to.join(','), subject, body);
        } catch (e) {
          console.log(ev.id + ': mail-outbound failed, SMTP fallback:', e.message);
          await smtpSend(host, port, user, pass, from, ev.to, subject, body);
        }
      } else {
        await smtpSend(host, port, user, pass, from, ev.to, subject, body);
      }
      state.sent[ev.id] = new Date().toISOString();
      changed = true;
      console.log(ev.id + ' sent -> ' + ev.to.join(', '));
      await tg(`📮 Follow-up ${ev.id} εστάλη (${ev.to.join(', ')})`);
    } catch (e) {
      console.log(ev.id + ' send failed: ' + e.message);
      await tg(`⚠️ ΑΠΟΤΥΧΙΑ follow-up ${ev.id}: ${e.message}`);
    }
  }
  if (changed) fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
})();
