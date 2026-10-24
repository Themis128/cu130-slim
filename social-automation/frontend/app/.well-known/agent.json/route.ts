export function GET() {
  const body=JSON.stringify({name:"SocialAuto Agent",description:"Social automation agent — creates, schedules and publishes social content across platforms.",url:"https://social.cloudless.gr/api/v1",version:"1.0.0",capabilities:{streaming:false,pushNotifications:false},skills:[{id:"create-post",name:"Create post",description:"Draft social content"},{id:"schedule-post",name:"Schedule post",description:"Schedule publishing"},{id:"analytics",name:"Analytics",description:"Read engagement metrics"}],defaultInputModes:["text"],defaultOutputModes:["text"],provider:{organization:"Cloudless",url:"https://cloudless.gr"}});
  const ctype="application/json";
  return new Response(body,{headers:{"content-type":ctype,"access-control-allow-origin":"*","cache-control":"public, max-age=3600"}});
}
