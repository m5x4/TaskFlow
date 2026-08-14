import "server-only";

import { createClient } from "@supabase/supabase-js";

/**
 * Server-side Supabase client.
 *
 * The `server-only` import at the top is load-bearing: if any Client Component
 * ever imports this module, the build fails rather than quietly shipping a
 * secret key to the browser.
 *
 * Neither environment variable is prefixed with NEXT_PUBLIC_, so Next.js will
 * not inline them into client JavaScript. This key bypasses row level security,
 * which is why every table in migrations/001_init.sql has RLS enabled with no
 * policies: a publishable/anon key can read nothing, and only this server-side
 * path can.
 *
 * SUPABASE_SECRET_KEY (sb_secret_...) is the current key type. The legacy
 * SUPABASE_SERVICE_ROLE_KEY is accepted as a fallback so existing projects keep
 * working — supabase-js treats the two identically. Supabase deprecates the
 * legacy keys at the end of 2026.
 */

const url = process.env.SUPABASE_URL;
const secretKey =
  process.env.SUPABASE_SECRET_KEY ?? process.env.SUPABASE_SERVICE_ROLE_KEY;

export const isConfigured = Boolean(url && secretKey);

export function getSupabase() {
  if (!url || !secretKey) {
    throw new Error(
      "Supabase is not configured. Copy web/.env.local.example to " +
        "web/.env.local and fill in SUPABASE_URL and SUPABASE_SECRET_KEY.",
    );
  }
  return createClient(url, secretKey, {
    auth: { persistSession: false, autoRefreshToken: false },
  });
}
