/**
 * Custom Next.js server with WebSocket proxy for noVNC.
 *
 * Why: Next.js rewrites do NOT support WebSocket upgrades
 * (https://github.com/vercel/next.js/issues/23147). noVNC needs a
 * WebSocket connection to websockify for VNC traffic. This custom server
 * intercepts WebSocket upgrade requests for /novnc/* and proxies them
 * to the browser-novnc container (port 6080), while forwarding all
 * other WebSocket upgrades (HMR, etc.) to Next.js's own upgrade handler.
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
 *
 *   Browser → wss://social.cloudless.gr/_next/webpack-hmr
 *   → Cloudflare Tunnel → social-frontend:8083 (this server)
 *   → Next.js upgrade handler → HMR WebSocket
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
  // We handle two types of WebSocket upgrades:
  //   1. /novnc/* → proxy to browser-novnc:6080 (websockify for VNC)
  //   2. everything else → Next.js's own upgrade handler (HMR in dev)
  //
  // Without forwarding non-novnc upgrades to Next.js, the HMR WebSocket
  // fails in dev mode, which prevents the page from hydrating and
  // leaves it stuck on the loading spinner.
  const nextUpgradeHandler = app.getUpgradeHandler()

  server.on('upgrade', (req, socket, head) => {
    const { pathname } = parse(req.url, true)

    if (pathname && pathname.startsWith('/novnc/')) {
      // noVNC WebSocket → proxy to browser-novnc:6080
      req.url = req.url.replace(/^\/novnc/, '') || '/'
      console.log(`[novnc-proxy] WS upgrade: ${pathname} → ${NOVNC_UPSTREAM}${req.url}`)
      novncProxy.ws(req, socket, head)
    } else {
      // All other WebSocket upgrades (HMR, etc.) → Next.js
      nextUpgradeHandler(req, socket, head)
    }
  })

  server.listen(PORT, HOSTNAME, () => {
    console.log(`[server] Ready on http://${HOSTNAME}:${PORT}`)
    console.log(`[novnc-proxy] HTTP + WS proxy: /novnc/* → ${NOVNC_UPSTREAM}`)
  })
})
