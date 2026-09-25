import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api";

export function LoginPage({ onLogin }: { onLogin: () => void }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      setPassword("");
      onLogin();
    } catch (err) {
      if (err instanceof ApiError && err.status === 429) {
        setError("Too many failed attempts. Wait a few minutes and try again.");
      } else if (err instanceof ApiError && err.status === 401) {
        setError("Wrong password.");
      } else {
        setError(err instanceof Error ? err.message : "Login failed.");
      }
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card login" onSubmit={submit}>
      <h1>Operator login</h1>
      <p className="muted">This app shows private captured data. It is only for the operator.</p>
      <label>
        Password
        <input
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
          autoFocus
        />
      </label>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <button type="submit" disabled={busy || !password}>
        {busy ? "Checking…" : "Log in"}
      </button>
    </form>
  );
}
