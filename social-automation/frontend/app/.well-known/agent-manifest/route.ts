export function GET() {
  const body=JSON.stringify({protocol:"PAP",version:"0.1",agentEndpoint:"https://social.cloudless.gr/api/v1",agentCard:"https://social.cloudless.gr/.well-known/agent.json",mcpServerCard:"https://social.cloudless.gr/.well-known/mcp/server-card.json",auth:"https://social.cloudless.gr/.well-known/oauth-authorization-server"});
  const ctype="application/json";
  return new Response(body,{headers:{"content-type":ctype,"access-control-allow-origin":"*","cache-control":"public, max-age=3600"}});
}
