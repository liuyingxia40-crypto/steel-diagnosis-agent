import { createServer } from "vite"

const server = await createServer({
  server: { middlewareMode: true },
  appType: "custom",
  logLevel: "error",
})

try {
  await server.ssrLoadModule("/check-hierarchy-lines.ts")
} finally {
  await server.close()
}
