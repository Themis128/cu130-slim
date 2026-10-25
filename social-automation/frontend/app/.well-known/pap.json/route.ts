export function GET() {
  const body=JSON.stringify({protocol:"PAP",version:"0.1",name:"SocialAuto Personal Agent",agentEndpoint:"https://social.cloudless.gr/api/v1",agentCard:"https://social.cloudless.gr/.well-known/agent.json",mcpServerCard:"https://social.cloudless.gr/.well-known/mcp/server-card.json",auth:"https://social.cloudless.gr/.well-known/oauth-authorization-server",capabilities:["create-post","schedule-post","analytics"],contact:"tbaltzakis@cloudless.gr"});
  return new Response(body,{headers:{"content-type":"application/json","access-control-allow-origin":"*","cache-control":"public, max-age=3600"}});
}
