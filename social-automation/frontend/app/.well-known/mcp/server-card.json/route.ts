export function GET() {
  const body=JSON.stringify({serverInfo:{name:"socialauto",title:"SocialAuto MCP Server",version:"1.0.0"},transport:{type:"stdio",note:"docker compose exec social-api python3 -m app.mcp.server; wraps https://social.cloudless.gr/api/v1"},capabilities:{tools:true,resources:true},tools:[{name:"create_post",description:"Create a social post draft"},{name:"schedule_post",description:"Schedule a post"},{name:"list_accounts",description:"List connected accounts"},{name:"list_media",description:"List media assets"}]});
  const ctype="application/json";
  return new Response(body,{headers:{"content-type":ctype,"access-control-allow-origin":"*","cache-control":"public, max-age=3600"}});
}
