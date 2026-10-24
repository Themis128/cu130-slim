// RFC 9727 API catalog — linkset+json
export function GET() {
  const body = JSON.stringify({ linkset: [{
    anchor: "https://social.cloudless.gr/api/v1",
    "service-desc": [{ href: "https://social.cloudless.gr/api/v1/openapi.json", type: "application/vnd.oai.openapi+json" }],
    "service-doc": [{ href: "https://social.cloudless.gr/api-docs", type: "text/html" }],
    status: [{ href: "https://social.cloudless.gr/api/v1/health" }],
  }]});
  const ctype = "application/linkset+json";
  return new Response(body, { headers: { "content-type": ctype, "access-control-allow-origin": "*" } });
}
