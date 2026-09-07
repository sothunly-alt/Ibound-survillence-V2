import { useMemo, useState, type FormEvent } from "react";
import { useAccount } from "../auth";
import { supabase, supabaseConfigured } from "../../lib/supabase";

type AuthMode = "signin" | "signup" | "forgot" | "code";

function modeFromUrl(): AuthMode {
  const params = new URLSearchParams(window.location.search);
  const mode = params.get("mode");
  if (mode === "signup") return "signup";
  if (mode === "reset") return "forgot";
  return "signin";
}

export function AuthScreen() {
  const { passwordRecovery, beginPasswordRecovery, finishPasswordRecovery } = useAccount();
  const [mode, setMode] = useState<AuthMode>(modeFromUrl);
  const [displayName, setDisplayName] = useState("");
  const [venueName, setVenueName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const settingNewPassword = passwordRecovery;
  const view: AuthMode | "set-password" = settingNewPassword ? "set-password" : mode;

  const title =
    view === "set-password" || view === "forgot" || view === "code"
      ? view === "set-password"
        ? "Set a new password"
        : "Reset password"
      : view === "signup"
        ? "Create account"
        : "Sign in";

  const copy =
    view === "set-password"
      ? "Choose a new password for this operator account. Use at least 8 characters."
      : view === "code"
        ? "Enter the 6-digit code from your email, then choose a new password. Type the code yourself — inbox scanners often burn one-click reset links."
        : view === "forgot"
          ? "We email a 6-digit code instead of a magic link. Codes survive email security scanners; one-click links often do not."
          : "Each account keeps profile, crew identities, ROI, camera protocol, and stream URLs private. Other operators cannot read them.";

  const redirectTo = useMemo(() => `${window.location.origin}/dashboard.html?mode=reset`, []);

  function go(next: AuthMode, nextNotice = "") {
    setMode(next);
    setError("");
    setNotice(nextNotice);
    setPassword("");
    setConfirmPassword("");
    if (next !== "code") setCode("");
  }

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (view !== "forgot") setNotice("");
    if (!supabaseConfigured) {
      setError("Add VITE_SUPABASE_ANON_KEY to .env, then restart the dev server.");
      return;
    }
    setBusy(true);
    try {
      if (view === "set-password") {
        if (password.length < 8) throw new Error("Password must be at least 8 characters.");
        if (password !== confirmPassword) throw new Error("Passwords do not match.");
        const { error: updateError } = await supabase.auth.updateUser({ password });
        if (updateError) throw updateError;
        finishPasswordRecovery();
        return;
      }
      if (view === "signup") {
        if (password.length < 8) throw new Error("Password must be at least 8 characters.");
        const { data, error: signError } = await supabase.auth.signUp({
          email: email.trim(),
          password,
          options: {
            emailRedirectTo: `${window.location.origin}/dashboard.html`,
            data: {
              display_name: displayName.trim(),
              venue_name: venueName.trim(),
            },
          },
        });
        if (signError) throw signError;
        if (!data.session) {
          setNotice("Account created. Confirm the email link, then sign in.");
        }
        return;
      }
      if (view === "forgot") {
        const { error: resetError } = await supabase.auth.resetPasswordForEmail(email.trim(), {
          redirectTo,
        });
        if (resetError) throw resetError;
        go("code", "If that email has an account, a 6-digit code is on its way.");
        return;
      }
      if (view === "code") {
        const token = code.replace(/\s/g, "");
        if (!/^\d{6}$/.test(token)) throw new Error("Enter the 6-digit code from the email.");
        if (password.length < 8) throw new Error("Password must be at least 8 characters.");
        if (password !== confirmPassword) throw new Error("Passwords do not match.");
        beginPasswordRecovery();
        const { error: otpError } = await supabase.auth.verifyOtp({
          email: email.trim(),
          token,
          type: "recovery",
        });
        if (otpError) {
          finishPasswordRecovery();
          throw otpError;
        }
        const { error: updateError } = await supabase.auth.updateUser({ password });
        if (updateError) throw updateError;
        finishPasswordRecovery();
        return;
      }
      const { error: signError } = await supabase.auth.signInWithPassword({
        email: email.trim(),
        password,
      });
      if (signError) throw signError;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed.");
    } finally {
      setBusy(false);
    }
  }

  const submitLabel =
    busy
      ? "Working…"
      : view === "set-password"
        ? "Save password"
        : view === "signup"
          ? "Create account"
          : view === "forgot"
            ? "Email reset code"
            : view === "code"
              ? "Set new password"
              : "Sign in";

  return (
    <div className="auth-screen">
      <form className="auth-card" onSubmit={(event) => void onSubmit(event)}>
        <div className="auth-card__brand">
          <img src="/inb_surveillance.png" alt="" width={36} height={36} />
          <div>
            <strong>Inbound Surveillance</strong>
            <span>Private operator console</span>
          </div>
        </div>
        <h1>{title}</h1>
        <p>{copy}</p>
        {!supabaseConfigured ? (
          <p className="auth-error">
            Supabase keys are missing. Set <code>VITE_SUPABASE_URL</code> and{" "}
            <code>VITE_SUPABASE_ANON_KEY</code> in <code>.env</code>.
          </p>
        ) : null}

        {view === "signup" ? (
          <>
            <label className="field">
              <span>Display name</span>
              <input
                value={displayName}
                onChange={(event) => setDisplayName(event.target.value)}
                placeholder="Operator name"
                autoComplete="name"
                required
              />
            </label>
            <label className="field">
              <span>Venue</span>
              <input
                value={venueName}
                onChange={(event) => setVenueName(event.target.value)}
                placeholder="Shop or site name"
                autoComplete="organization"
              />
            </label>
          </>
        ) : null}

        {view !== "set-password" ? (
          <label className="field">
            <span>Email</span>
            <input
              type="email"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@garage.com"
              autoComplete="email"
              required
              readOnly={view === "code"}
            />
          </label>
        ) : null}

        {view === "code" ? (
          <label className="field">
            <span>Reset code</span>
            <input
              className="otp-input"
              value={code}
              onChange={(event) => setCode(event.target.value.replace(/[^\d]/g, "").slice(0, 6))}
              placeholder="6-digit code"
              inputMode="numeric"
              autoComplete="one-time-code"
              required
            />
          </label>
        ) : null}

        {view === "signin" || view === "signup" || view === "code" || view === "set-password" ? (
          <label className="field">
            <span>{view === "signin" ? "Password" : "New password"}</span>
            <input
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder={view === "signin" ? "Password" : "At least 8 characters"}
              autoComplete={view === "signin" ? "current-password" : "new-password"}
              required
            />
          </label>
        ) : null}

        {view === "code" || view === "set-password" ? (
          <label className="field">
            <span>Confirm password</span>
            <input
              type="password"
              value={confirmPassword}
              onChange={(event) => setConfirmPassword(event.target.value)}
              placeholder="Repeat new password"
              autoComplete="new-password"
              required
            />
          </label>
        ) : null}

        {error ? <p className="auth-error">{error}</p> : null}
        {notice ? <p className="auth-notice">{notice}</p> : null}
        <button className="btn btn--primary" type="submit" disabled={busy || !supabaseConfigured}>
          {submitLabel}
        </button>
        {view === "signin" ? (
          <button className="btn btn--ghost" type="button" onClick={() => go("forgot")}>
            Forgot password?
          </button>
        ) : null}
        {view === "code" ? (
          <button className="btn btn--ghost" type="button" onClick={() => go("forgot")}>
            Send a new code
          </button>
        ) : null}
        {view !== "set-password" ? (
          <button
            className="btn btn--ghost"
            type="button"
            onClick={() => go(view === "signup" ? "signin" : view === "signin" ? "signup" : "signin")}
          >
            {view === "signup"
              ? "Already have an account? Sign in"
              : view === "signin"
                ? "Create account"
                : "Back to sign in"}
          </button>
        ) : null}
      </form>
    </div>
  );
}

export function AuthLoading() {
  return (
    <div className="auth-screen">
      <p className="auth-loading">Loading operator console…</p>
    </div>
  );
}
