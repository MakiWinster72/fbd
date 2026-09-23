import { NextRequest } from "next/server";

export const runtime = "nodejs";

// Next's generic rewrite proxy stalls on large multipart requests. Stream this
// upload route directly to the API instead of buffering it through the rewrite.
async function forward(request: NextRequest) {
  const upstream = `${process.env.RAG_API_BASE_URL ?? "http://127.0.0.1:8080"}/api/v1/admin/document-imports`;
  try {
    const response = await fetch(upstream, {
      method: request.method,
      headers: {
        ...(request.headers.get("content-type") ? { "content-type": request.headers.get("content-type")! } : {}),
        ...(request.headers.get("cookie") ? { cookie: request.headers.get("cookie")! } : {}),
      },
      ...(request.method === "POST" ? { body: request.body, duplex: "half" as const } : {}),
      cache: "no-store",
    } as RequestInit & { duplex?: "half" });
    return new Response(response.body, {
      status: response.status,
      headers: { "content-type": response.headers.get("content-type") ?? "application/json" },
    });
  } catch {
    return Response.json({ detail: "上传代理无法连接后端服务" }, { status: 502 });
  }
}

export const POST = forward;
export const GET = forward;
