export const runtime = "nodejs";

// Model inference may exceed the rewrite proxy's fixed 30-second timeout.
// Use a route handler so the backend can return its real result or error.
export async function POST(request: Request) {
  const upstream = `${process.env.RAG_API_BASE_URL ?? "http://127.0.0.1:8080"}/api/v1/chat`;
  try {
    const response = await fetch(upstream, {
      method: "POST",
      headers: {
        "content-type": request.headers.get("content-type") ?? "application/json",
        ...(request.headers.get("cookie") ? { cookie: request.headers.get("cookie")! } : {}),
      },
      body: await request.text(),
      cache: "no-store",
      redirect: "error",
    });
    return new Response(response.body, {
      status: response.status,
      headers: {
        "content-type": response.headers.get("content-type") ?? "application/json",
        "cache-control": "no-store",
      },
    });
  } catch {
    return Response.json({ detail: "聊天代理无法连接后端服务，请稍后重试" }, { status: 502 });
  }
}
