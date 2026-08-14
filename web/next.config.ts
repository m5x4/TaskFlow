import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The service-role key must never be inlined into a client bundle. Keeping it
  // out of `env` here (and reading it only inside server code) is what makes
  // that true — see lib/supabase.ts.
  reactStrictMode: true,

  // Without this, Next walks up and finds an unrelated lockfile in the home
  // directory and infers the wrong workspace root.
  outputFileTracingRoot: path.join(import.meta.dirname, ".."),
};

export default nextConfig;
