import type { NextConfig } from "next";

// FastAPI (kev.serve) is proxied under /kev so the browser never deals with CORS or ports.
const KEV_API = process.env.KEV_API ?? "http://127.0.0.1:8009";

const nextConfig: NextConfig = {
  reactCompiler: true,
  devIndicators: false,
  // Next dev only trusts the hostname it was started with (localhost); without this,
  // Other local access addresses need an explicit entry so client scripts and HMR can load.
  allowedDevOrigins: ["127.0.0.1", "192.168.10.157"],
  async rewrites() {
    return [{ source: "/kev/:path*", destination: `${KEV_API}/:path*` }];
  },
};

export default nextConfig;
