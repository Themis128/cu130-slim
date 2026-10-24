"use client";

// WebMCP — expose site tools to in-browser AI agents (draft spec).
// registerTool() returns a Promise; an AbortSignal detaches the tools on unmount.
import { useEffect } from "react";

export function WebMcpTools() {
  useEffect(() => {
    const mc = (document as unknown as { modelContext?: {
      registerTool: (t: object, o?: { signal?: AbortSignal }) => Promise<unknown>;
    } }).modelContext;
    if (!mc) return;
    const ac = new AbortController();
    const opts = { signal: ac.signal };
    void mc.registerTool({
      name: "list_platforms",
      description: "List the social platforms SocialAuto can publish to",
      inputSchema: { type: "object", properties: {} },
      execute: async () => ({
        platforms: ["linkedin", "x", "instagram", "facebook", "threads", "tiktok", "telegram", "whatsapp"],
      }),
    }, opts);
    void mc.registerTool({
      name: "check_api_health",
      description: "Check whether the SocialAuto API is healthy",
      inputSchema: { type: "object", properties: {} },
      execute: async () => {
        const r = await fetch("/api/v1/health");
        return { status: r.status, ok: r.ok };
      },
    }, opts);
    return () => ac.abort();
  }, []);
  return null;
}
