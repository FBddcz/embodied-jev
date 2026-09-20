import { defineConfig } from "vite";
export default defineConfig({
  root: "frontend",
  build: { outDir: "../src/embodied_jev/web", emptyOutDir: true },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8090",
        configure(proxy) {
          proxy.on("proxyReq", (req) =>
            req.setHeader("origin", "http://127.0.0.1:8090"),
          );
        },
      },
    },
  },
});
