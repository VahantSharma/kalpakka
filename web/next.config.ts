import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  agentRules: false, // do not generate tool-instruction files in the repo
};

export default nextConfig;
