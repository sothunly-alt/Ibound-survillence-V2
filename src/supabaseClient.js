import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

export const supabase =
  supabaseUrl && supabaseAnonKey ? createClient(supabaseUrl, supabaseAnonKey) : null;

function showError(form, message) {
  const err = document.getElementById("form-error");
  if (!err) return;
  err.hidden = false;
  err.textContent = message;
}

function showSuccess() {
  const form = document.getElementById("demo-form");
  const success = document.getElementById("form-success");
  if (form) form.hidden = true;
  if (success) {
    success.hidden = false;
    success.textContent = "Thanks, we'll be in touch";
  }
}

const form = document.getElementById("demo-form");
form?.addEventListener("submit", async (event) => {
  event.preventDefault();

  const submitBtn = form.querySelector('[type="submit"]');
  const err = document.getElementById("form-error");
  if (err) {
    err.hidden = true;
    err.textContent = "";
  }

  const data = new FormData(form);
  const name = String(data.get("name") || "").trim();
  const email = String(data.get("email") || "").trim();
  const company = String(data.get("company") || data.get("shop") || "").trim();

  const idleLabel = submitBtn?.textContent || "[ BOOK DEMO ]";
  form.setAttribute("aria-busy", "true");
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Sending…";
  }

  try {
    if (!supabase) {
      throw new Error("Supabase is not configured.");
    }

    const { error } = await supabase.from("demo_requests").insert({ name, email, company });
    if (error) throw error;
    showSuccess();
  } catch (error) {
    console.error(error);
    showError(form, error?.message || "Could not send your request. Try again.");
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = idleLabel;
    }
  } finally {
    form.removeAttribute("aria-busy");
  }
});
