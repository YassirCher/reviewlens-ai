"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { ArrowRight, LockKeyhole } from "lucide-react";
import { adminRequest, clearAdminCsrf } from "@/lib/admin";

function LoginForm() {
  const router = useRouter(); const search = useSearchParams();
  const [email, setEmail] = useState(""); const [password, setPassword] = useState("");
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setError(""); setBusy(true);
    try {
      await adminRequest("/session", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }) });
      clearAdminCsrf();
      const next = search.get("next"); router.replace(next?.startsWith("/admin/") && !next.startsWith("//") ? next : "/admin");
    } catch (value) { setError(value instanceof Error ? value.message : "Sign in failed."); }
    finally { setBusy(false); }
  }
  return <main className="admin-login"><div className="admin-login-intro"><span className="admin-brand-mark">R</span><p className="v2-eyebrow">REVIEWLENS / ADMIN</p><h1>Control the system with evidence.</h1><p>Inspect runs, govern published versions, and monitor costs from one protected workspace.</p></div><form onSubmit={submit} className="admin-login-card"><LockKeyhole size={22} aria-hidden="true" /><h2>Admin sign in</h2><p>Use your single administrator account.</p><label htmlFor="admin-email">Email</label><input id="admin-email" type="email" autoComplete="username" required value={email} onChange={e => setEmail(e.target.value)} /><label htmlFor="admin-password">Password</label><input id="admin-password" type="password" autoComplete="current-password" required value={password} onChange={e => setPassword(e.target.value)} /><p className="admin-form-error" role="alert">{error}</p><button className="admin-primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}<ArrowRight size={16} aria-hidden="true" /></button></form></main>;
}
export default function Page() { return <Suspense fallback={<main className="admin-gate">Loading sign in…</main>}><LoginForm /></Suspense>; }
