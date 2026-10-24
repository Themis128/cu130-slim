export function GET() {
  const body=JSON.stringify({specVersion:"0.1.0",host:{domain:"social.cloudless.gr",name:"SocialAuto",description:"Multi-platform social automation API"},entries:[
    {id:"urn:air:social.cloudless.gr:api:socialauto",identifier:"urn:air:social.cloudless.gr:api:socialauto",uri:"urn:air:social.cloudless.gr:api:socialauto",displayName:"SocialAuto REST API",type:"application/vnd.oai.openapi+json",url:"https://social.cloudless.gr/api/v1/openapi.json",representativeQueries:["create a post", "schedule content", "list accounts", "upload media"]},
    {id:"urn:air:social.cloudless.gr:mcp:socialauto",identifier:"urn:air:social.cloudless.gr:mcp:socialauto",uri:"urn:air:social.cloudless.gr:mcp:socialauto",displayName:"SocialAuto MCP server",type:"application/mcp+json",url:"https://social.cloudless.gr/.well-known/mcp/server-card.json",representativeQueries:["posting tools", "scheduling tools"]},
    {id:"urn:air:social.cloudless.gr:a2a:assistant",identifier:"urn:air:social.cloudless.gr:a2a:assistant",uri:"urn:air:social.cloudless.gr:a2a:assistant",displayName:"SocialAuto A2A agent",type:"application/json",url:"https://social.cloudless.gr/.well-known/agent.json",representativeQueries:["agent capabilities"]},
    {id:"urn:air:social.cloudless.gr:doc:api-docs",identifier:"urn:air:social.cloudless.gr:doc:api-docs",uri:"urn:air:social.cloudless.gr:doc:api-docs",displayName:"API documentation",type:"text/html",url:"https://social.cloudless.gr/api-docs",representativeQueries:["API reference"]},
    {id:"urn:air:social.cloudless.gr:svc:health",identifier:"urn:air:social.cloudless.gr:svc:health",uri:"urn:air:social.cloudless.gr:svc:health",displayName:"Service health",type:"application/json",url:"https://social.cloudless.gr/api/v1/health",representativeQueries:["health check"]}
  ]});
  return new Response(body,{headers:{"content-type":"application/json","access-control-allow-origin":"*","cache-control":"public, max-age=3600"}});
}
