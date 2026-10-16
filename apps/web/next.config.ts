import type { NextConfig } from "next";
import { networkInterfaces } from "node:os";

const apiBaseUrl = process.env.RAG_API_BASE_URL ?? "http://127.0.0.1:8080";

const nextConfig: NextConfig = {
  // Allow only this machine's addresses, not arbitrary cross-origin dev access.
  allowedDevOrigins: [
    ...new Set(
      Object.values(networkInterfaces())
        .flatMap((addresses) => addresses ?? [])
        .filter((address) => address.family === "IPv4")
        .map((address) => address.address),
    ),
  ],
  async rewrites() {
    return [
      {
        source: "/rag-api/:path*",
        destination: `${apiBaseUrl}/:path*`,
      },
    ];
  },
};

export default nextConfig;
