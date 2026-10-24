export function GET() {
  const body=JSON.stringify({resource:"https://social.cloudless.gr/api/v1",authorization_servers:["https://social.cloudless.gr"],scopes_supported:["read","write","admin"],bearer_methods_supported:["header"],resource_documentation:"https://social.cloudless.gr/api-docs"});
  const ctype="application/json";
  return new Response(body,{headers:{"content-type":ctype,"access-control-allow-origin":"*","cache-control":"public, max-age=3600"}});
}
