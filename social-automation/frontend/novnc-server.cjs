/**
 * Front proxy server with WebSocket support for noVNC.
 *
 * Why: Next.js rewrites do NOT support WebSocket upgrades
 * (https://github.com/vercel/next.js/issues/23147). noVNC needs a
 * WebSocket connection to websockify for VNC traffic. This server sits
 * in front of the standalone Next.js server (server.js, spawned as a
 * child process) and intercepts /novnc/* traffic — HTTP and WebSocket —
 * proxying it to the browser-novnc container (port 6080).
 *
 * Compatible with the standalone output image: it does not require the
 * `next` package at runtime. Its only external dependency is
 * `http-proxy` (MIT), copied into the image by the Dockerfile.
 *
 * Flow:
 *   Browser → wss://social.cloudless.gr/novnc/websockify
 *   → Cloudflare Tunnel → social-frontend:8083 (this server)
 *   → http-proxy WS upgrade → browser-novnc:6080/websockify
 *   → websockify → x11vnc → Xvfb display
 *
 *   Browser → https://social.cloudless.gr/anything-else
 *   → Cloudflare Tunnel → social-frontend:8083 (this server)
 *   → http-proxy → 127.0.0.1:NEXT_INTERNAL_PORT (standalone server.js)
 */
const { createServer } = require('http')
const { parse } = require('url')
const { spawn } = require('child_process')
const httpProxy = require('http-proxy')

const NOVNC_UPSTREAM = process.env.NOVNC_UPSTREAM_URL || 'http://browser-novnc:6080'
const PORT = parseInt(process.env.PORT || '8083', 10)
const HOSTNAME = process.env.HOSTNAME || '0.0.0.0'
const NEXT_PORT = parseInt(process.env.NEXT_INTERNAL_PORT || '8099', 10)

// Standalone Next.js server as a child process (self-contained, no
// `next` module needed). Bound to loopback only — this front proxy is
// the sole public entrypoint.
const nextChild = spawn('node', ['server.js'], {
  env: { ...process.env, PORT: String(NEXT_PORT), HOSTNAME: '127.0.0.1' },
  stdio: 'inherit',
})
nextChild.on('exit', (code) => {
  console.error(`[server] next.js child exited with code ${code} — shutting down`)
  process.exit(code ?? 1)
})

const nextProxy = httpProxy.createProxyServer({
  target: `http://127.0.0.1:${NEXT_PORT}`,
  ws: true,
  xfwd: true,
})

const novncProxy = httpProxy.createProxyServer({
  target: NOVNC_UPSTREAM,
  ws: true,            // enable WebSocket proxying
  changeOrigin: true,  // set Host header to upstream
  xfwd: true,          // add X-Forwarded-* headers
})

function proxyError(name) {
  return (err, req, res) => {
    const target = req && req.url ? req.url : '?'
    console.error(`[${name}] error for ${target}: ${err.message}`)
    if (res && !res.headersSent && res.writeHead) {
      res.writeHead(502, { 'Content-Type': 'text/plain' })
      res.end(`${name}: upstream unavailable`)
    }
  }
}
novncProxy.on('error', proxyError('novnc-proxy'))
nextProxy.on('error', proxyError('next-proxy'))

const server = createServer((req, res) => {
  const { pathname } = parse(req.url, true)

  // Proxy noVNC HTTP requests (HTML, JS, CSS, images) to browser-novnc
  if (pathname && pathname.startsWith('/novnc/')) {
    // Strip /novnc prefix: /novnc/vnc.html → /vnc.html
    req.url = req.url.replace(/^\/novnc/, '') || '/'
    novncProxy.web(req, res)
    return
  }

  // All other requests go to the standalone Next.js server
  nextProxy.web(req, res)
})

server.on('upgrade', (req, socket, head) => {
  const { pathname } = parse(req.url, true)

  if (pathname && pathname.startsWith('/novnc/')) {
    // noVNC WebSocket → proxy to browser-novnc:6080
    req.url = req.url.replace(/^\/novnc/, '') || '/'
    console.log(`[novnc-proxy] WS upgrade: ${pathname} → ${NOVNC_UPSTREAM}${req.url}`)
    novncProxy.ws(req, socket, head)
  } else {
    // All other WebSocket upgrades → standalone Next.js server
    nextProxy.ws(req, socket, head)
  }
})

for (const sig of ['SIGTERM', 'SIGINT']) {
  process.on(sig, () => {
    nextChild.kill('SIGTERM')
    server.close(() => process.exit(0))
    setTimeout(() => process.exit(0), 5000).unref()
  })
}

server.listen(PORT, HOSTNAME, () => {
  console.log(`[server] Ready on http://${HOSTNAME}:${PORT}`)
  console.log(`[novnc-proxy] HTTP + WS proxy: /novnc/* → ${NOVNC_UPSTREAM}`)
  console.log(`[next-proxy] all other traffic → http://127.0.0.1:${NEXT_PORT}`)
})
