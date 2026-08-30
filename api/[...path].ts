/**
 * Proxies /api/* → Render repro API, injecting REPRO_API_TOKEN on mutating requests.
 * Mirrors server/index.ts for Vercel serverless.
 *
 * The upstream body is passed through unbuffered, which is what keeps
 * /runs/<job>/events usable as an event stream. The edge runtime still caps how long a
 * single invocation may run, so a long run's stream will be cut: the client resumes
 * with ?after=<last event id> rather than replaying, so a cut costs a reconnect and
 * nothing else.
 */
export const config = { runtime: "edge" };

export default async function handler(request: Request): Promise<Response> {
  const origin = (process.env.REPRO_API_ORIGIN || "https://daytona-repro-api.onrender.com").replace(
    /\/+$/,
    "",
  );
  const token = process.env.REPRO_API_TOKEN || "";

  const incoming = new URL(request.url);
  const upstreamPath = incoming.pathname.replace(/^\/api/, "") || "/";
  const target = new URL(upstreamPath + incoming.search, origin + "/");

  const headers = new Headers(request.headers);
  headers.set("host", target.host);
  headers.delete("content-length");

  if (token && (request.method === "POST" || request.method === "PUT" || request.method === "PATCH")) {
    headers.set("authorization", `Bearer ${token}`);
  }

  try {
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
    });
    const out = new Headers(upstream.headers);
    out.delete("content-encoding");
    out.delete("transfer-encoding");
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: out,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return Response.json({ error: `repro API unreachable: ${message}` }, { status: 502 });
  }
}
