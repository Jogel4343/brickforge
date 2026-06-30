/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Three.js + LDrawLoader ship some legacy CommonJS modules; transpile them.
  transpilePackages: ["three"],
  experimental: {
    // Enables larger server-side payloads for LDraw model files (we serve some via API routes).
    serverActions: { bodySizeLimit: "20mb" },
  },
};
export default nextConfig;
