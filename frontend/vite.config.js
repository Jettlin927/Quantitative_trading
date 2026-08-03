// @ts-nocheck
import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

function readPersonalGatewayToken(filePath) {
  if (!filePath) return "";
  try {
    return readFileSync(filePath, "utf8").trim();
  } catch {
    return "";
  }
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, ".", "");
  const personalGatewayToken = readPersonalGatewayToken(env.PERSONAL_GATEWAY_TOKEN_FILE);
  return {
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port: 5173,
      strictPort: true,
      proxy: {
        "/api/personal": {
          target: env.VITE_API_PROXY_TARGET || "http://127.0.0.1:18000",
          changeOrigin: true,
          headers: personalGatewayToken ? { "X-Personal-Gateway": personalGatewayToken } : {},
        },
        "/api": {
          target: env.VITE_API_PROXY_TARGET || "http://127.0.0.1:18000",
          changeOrigin: true,
        },
      },
    },
    preview: {
      host: "0.0.0.0",
      port: 5173,
    },
    test: {
      environment: "jsdom",
    },
  };
});
