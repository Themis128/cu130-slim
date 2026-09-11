/**
 * Custom Next.js server with WebSocket proxy for noVNC.
 *
 * Why: Next.js rewrites do NOT support WebSocket upgrades
 * (https://github.com/vercel/next.js/issues/23147). noVNC needs a
 * WebSocket connection to websockify for VNC traffic. This custom server
 * intercepts WebSocket upgrade requests for /novnc/* and proxies them
 * to the browser-novnc container (port 6080), while all other HTTP
 * requests are delegated to Next.js normally.
 *
 * This keeps noVNC same-origin (HTTPS) — no Mixed Content warnings —
 * and the VNC WebSocket connection works through the Cloudflare Tunnel
 * which supports wss natively.
 *
 * Free + open-source: uses http-proxy (MIT, 14k stars, same library
 * Next.js uses internally) — no additional Docker containers required.
 *
 * Flow:
 *   Browser → wss://social.cloudless.gr/novnc/websockify
 *   → Cloudflare Tunnel → social-frontend:8083 (this server)
 *   → http-proxy WS upgrade → browser-novnc:6080/websockify
 *   → websockify → x11vnc → Xvfb display
 */
const { createServer } = require('http')
const { parse } = require('url')
const httpProxy = require('http-proxy')

const NOVNC_UPSTREAM = process.env.NOVNC_UPSTREAM_URL || 'http://browser-novnc:6080'
const PORT = parseInt(process.env.PORT || '8083', 10)
const HOSTNAME = process.env.HOSTNAME || '0.0.0.0'

// Start Next.js
const next = require('next')
const app = next({ dev: process.env.NODE_ENV !== 'production', hostname: HOSTNAME, port: PORT })
const handle = app.getRequestHandler()

// Create a proxy for noVNC WebSocket + HTTP traffic
const novncProxy = httpProxy.createProxyServer({
  target: NOVNC_UPSTREAM,
  ws: true,            // enable WebSocket proxying
  changeOrigin: true,  // set Host header to upstream
  xfwd: true,          // add X-Forwarded-* headers
})

novncProxy.on('error', (err, req, res) => {
  const target = req && req.url ? req.url : '?'
  console.error(`[novnc-proxy] error for ${target}: ${err.message}`)
  if (res && !res.headersSent) {
    if (res.writeHead) {
      res.writeHead(502, { 'Content-Type': 'text/plain' })
      res.end('noVNC proxy: upstream unavailable')
    }
  }
})

app.prepare().then(() => {
  const server = createServer((req, res) => {
    const parsedUrl = parse(req.url, true)
    const { pathname } = parsedUrl

    // Proxy noVNC HTTP requests (HTML, JS, CSS, images) to browser-novnc
    // The Next.js rewrite also handles this, but we keep it here as a
    // fallback in case rewrites don't fire in custom server mode.
    if (pathname && pathname.startsWith('/novnc/')) {
      // Strip /novnc prefix: /novnc/vnc.html → /vnc.html
      req.url = req.url.replace(/^\/novnc/, '') || '/'
      novncProxy.web(req, res)
      return
    }

    // All other requests go to Next.js
    handle(req, res, parsedUrl)
  })

  // ── WebSocket upgrade handler ──────────────────────────────────────
  // noVNC connects to /novnc/websockify for the VNC WebSocket.
  // We proxy the upgrade to browser-novnc:6080 (websockify).
  server.on('upgrade', (req, socket, head) => {
    const { pathname } = parse(req.url, true)

    // Only proxy noVNC WebSocket upgrades
    if (!pathname || !pathname.startsWith('/novnc/')) {
      // Not a noVNC WebSocket — Next.js dev server handles HMR etc.
      return
    }

    // Strip /novnc prefix: /novnc/websockify → /websockify
    req.url = req.url.replace(/^\/novnc/, '') || '/'

    console.log(`[novnc-proxy] WS upgrade: ${pathname} → ${NOVNC_UPSTREAM}${req.url}`)

    // http-proxy handles the full WebSocket handshake (101 Switching
    // Protocols), header forwarding, and bidirectional piping.
    novncProxy.ws(req, socket, head)
  })

  server.listen(PORT, HOSTNAME, () => {
    console.log(`[server] Ready on http://${HOSTNAME}:${PORT}`)
    console.log(`[novnc-proxy] HTTP + WS proxy: /novnc/* → ${NOVNC_UPSTREAM}`)
  })
})
