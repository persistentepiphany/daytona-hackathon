/**
 * Proxies /api/* → Render repro API, injecting REPRO_API_TOKEN on mutating requests.
 * Mirrors server/index.ts for Vercel serverless.
 *
 * Routed via vercel.json rewrite so nested paths (/api/runs/:id) work with Vite.
 */
export const config = { runtime: "edge" };

function upstreamPathFrom(request: Request): string {
  const url = new URL(request.url);
  // Rewrite destination may be /api/proxy?__path=runs/id — prefer that.
  const rewritten = url.searchParams.get("__path");
  if (rewritten != null) {
    const q = new URLSearchParams(url.searchParams);
    q.delete("__path");
    const qs = q.toString();
    return `/${rewritten.replace(/^\/+/, "")}${qs ? `?${qs}` : ""}`;
  }
  // Direct hit or passthrough: strip /api or /api/proxy
  let path = url.pathname.replace(/^\/api(?:\/proxy)?/, "") || "/";
  if (!path.startsWith("/")) path = `/${path}`;
  return `${path}${url.search}`;
}

export default async function handler(request: Request): Promise<Response> {
  const origin = (process.env.REPRO_API_ORIGIN || "https://daytona-repro-api.onrender.com").replace(
    /\/+$/,
    "",
  );
  const token = process.env.REPRO_API_TOKEN || "";

  const upstreamPath = upstreamPathFrom(request);
  const target = new URL(upstreamPath, origin + "/");

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
