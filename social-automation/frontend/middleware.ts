import { NextRequest, NextResponse } from "next/server";

const HOME_MD = `# SocialAuto

Multi-platform social media automation — create, schedule and publish
content across LinkedIn, X, Instagram, Facebook, Threads, TikTok, Telegram
and WhatsApp from one API.

## Agent surface
- REST API: /api/v1 (OpenAPI: /api/v1/openapi.json)
- API catalog: /.well-known/api-catalog
- MCP server card: /.well-known/mcp/server-card.json
- A2A agent card: /.well-known/agent.json
- ARD manifest: /.well-known/ai-catalog.json
- Auth: /.well-known/oauth-authorization-server · /auth.md
`;

export function middleware(req: NextRequest) {
  const accept = req.headers.get("accept") ?? "";
  if (accept.includes("text/markdown")) {
    return new NextResponse(HOME_MD, {
      headers: {
        "content-type": "text/markdown; charset=utf-8",
        "x-markdown-tokens": String((HOME_MD.length / 4) | 0),
        vary: "Accept",
      },
    });
  }
  return NextResponse.next();
}

export const config = { matcher: "/" };
