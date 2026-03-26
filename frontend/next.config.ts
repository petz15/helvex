import type { NextConfig } from "next";

const FASTAPI_URL = process.env.FASTAPI_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${FASTAPI_URL}/api/:path*` },
      { source: "/health", destination: `${FASTAPI_URL}/health` },
      { source: "/static/:path*", destination: `${FASTAPI_URL}/static/:path*` },
    ];
  },
};

export default nextConfig;
