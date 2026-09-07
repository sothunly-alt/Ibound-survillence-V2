import { createClient } from "@supabase/supabase-js";
import type { Database } from "./database.types";

export const SUPABASE_URL = (import.meta.env.VITE_SUPABASE_URL || "").trim();
export const SUPABASE_ANON_KEY = (
  import.meta.env.VITE_SUPABASE_ANON_KEY ||
  import.meta.env.VITE_SUPABASE_PUBLISHABLE_KEY ||
  ""
).trim();

export const supabaseConfigured = Boolean(SUPABASE_URL && SUPABASE_ANON_KEY);
export const ACCOUNT_BUCKET = "account-private";

export const supabase = createClient<Database>(
  SUPABASE_URL || "https://unavailable.supabase.co",
  SUPABASE_ANON_KEY || "missing-anon-key",
  {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: true,
      flowType: "pkce",
    },
  },
);
