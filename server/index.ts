import express from "express";
import { createServer } from "http";
import http from "http";
import https from "https";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const API_ORIGIN = (process.env.REPRO_API_ORIGIN || "https://daytona-repro-api.onrender.com").replace(
  /\/+$/,
  "",
);
const API_TOKEN = process.env.REPRO_API_TOKEN || "";

function proxyApi(req: express.Request, res: express.Response) {
  const upstreamPath = req.originalUrl.replace(/^\/api/, "") || "/";
  const target = new URL(upstreamPath, API_ORIGIN);
  const transport = target.protocol === "https:" ? https : http;
  const headers: http.OutgoingHttpHeaders = {
    ...req.headers,
    host: target.host,
  };
  if (API_TOKEN && (req.method === "POST" || req.method === "PUT" || req.method === "PATCH")) {
    headers.authorization = `Bearer ${API_TOKEN}`;
  }
  delete headers["content-length"];

  const upstream = transport.request(
    target,
    { method: req.method, headers },
    (up) => {
      res.writeHead(up.statusCode || 502, up.headers);
      // the run feed is an endless text/event-stream: send the headers now and keep the
      // socket open, or the browser sees nothing until the run ends
      res.flushHeaders();
      res.setTimeout(0);
      up.pipe(res);
    },
  );
  upstream.on("error", (err) => {
    res.status(502).json({ error: `repro API unreachable: ${err.message}` });
  });
  if (req.method === "POST" || req.method === "PUT" || req.method === "PATCH") {
    req.pipe(upstream);
  } else {
    upstream.end();
  }
}

async function startServer() {
  const app = express();
  const server = createServer(app);

  app.use("/api", proxyApi);

  const staticPath =
    process.env.NODE_ENV === "production"
      ? path.resolve(__dirname, "public")
      : path.resolve(__dirname, "..", "dist", "public");

  app.use(express.static(staticPath));

  app.get("*", (_req, res) => {
    res.sendFile(path.join(staticPath, "index.html"));
  });

  const port = process.env.PORT || 3000;

  server.listen(port, () => {
    console.log(`Server running on http://localhost:${port}/`);
    console.log(`Proxying /api → ${API_ORIGIN}`);
  });
}

startServer().catch(console.error);
