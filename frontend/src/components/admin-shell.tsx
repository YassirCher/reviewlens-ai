"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Activity, BookOpen, Bot, ChartNoAxesCombined, Command, GitBranch, LayoutDashboard, LogOut, Menu, Network, ScrollText, Settings, Wrench, X } from "lucide-react";
import type { ReactNode } from "react";
import { adminGet, adminRequest, clearAdminCsrf, type AdminSession } from "@/lib/admin";

const nav = [
  { href: "/admin", label: "Overview", icon: LayoutDashboard },
  { href: "/admin/runs", label: "Runs", icon: Activity },
  { href: "/admin/agents", label: "Agents", icon: Bot },
  { href: "/admin/workflows", label: "Workflows", icon: GitBranch },
  { href: "/admin/models", label: "Models", icon: Network },
  { href: "/admin/tools", label: "Tools", icon: Wrench },
  { href: "/admin/knowledge", label: "Knowledge", icon: BookOpen },
  { href: "/admin/analytics", label: "Analytics", icon: ChartNoAxesCombined },
  { href: "/admin/settings", label: "Settings", icon: Settings },
  { href: "/admin/audit", label: "Audit", icon: ScrollText },
];

export function AdminShell({ children }: { children: ReactNode }) {
  const path = usePathname(); const router = useRouter();
  const [session, setSession] = useState<AdminSession | null>(null);
  const [state, setState] = useState<"loading" | "ready" | "error">("loading");
  const [palette, setPalette] = useState(false); const [menu, setMenu] = useState(false);
  useEffect(() => {
    if (path === "/admin/login") return;
    let active = true;
    adminGet<AdminSession>("/session").then(value => { if (active) { setSession(value); setState("ready"); } })
      .catch(error => { if (!active) return; if (error.status === 401) router.replace(`/admin/login?next=${encodeURIComponent(path)}`); else setState("error"); });
    return () => { active = false; };
  }, [path, router]);
  useEffect(() => {
    const listener = (event: KeyboardEvent) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") { event.preventDefault(); setPalette(true); } };
    window.addEventListener("keydown", listener); return () => window.removeEventListener("keydown", listener);
  }, []);
  if (path === "/admin/login") return <div className="v2 admin admin-login-shell">{children}</div>;
  if (state === "loading") return <div className="v2 admin admin-gate" role="status">Checking admin session…</div>;
  if (state === "error") return <div className="v2 admin admin-gate" role="alert">The admin session could not be checked. <button onClick={() => location.reload()}>Retry</button></div>;
  async function logout() { try { await adminRequest<void>("/session", { method: "DELETE" }, true); } finally { clearAdminCsrf(); router.replace("/admin/login"); } }
  return <div className="v2 admin">
    <a className="v2-skip" href="#admin-main">Skip to main content</a>
    <aside className={`admin-sidebar ${menu ? "admin-sidebar-open" : ""}`} aria-label="Admin navigation">
      <Link className="admin-brand" href="/admin"><span className="admin-brand-mark">R</span><span>ReviewLens<small>CONTROL PLANE</small></span></Link>
      <nav aria-label="Admin sections">{nav.map(item => <Link key={item.href} href={item.href} onClick={() => setMenu(false)} aria-current={path === item.href || (item.href !== "/admin" && path.startsWith(`${item.href}/`)) ? "page" : undefined}><item.icon size={17} aria-hidden="true" />{item.label}</Link>)}</nav>
      <div className="admin-sidebar-bottom"><span title={session?.admin.email}>{session?.admin.email}</span><button onClick={logout}><LogOut size={16} aria-hidden="true" /> Sign out</button></div>
    </aside>
    <div className="admin-main-column">
      <header className="admin-topbar"><button className="admin-mobile-menu" aria-label="Open navigation" onClick={() => setMenu(!menu)}><Menu size={19} /></button><span>Admin / {nav.find(item => item.href !== "/admin" && path.startsWith(item.href))?.label || "Overview"}</span><button className="admin-command" onClick={() => setPalette(true)}><Command size={15} aria-hidden="true" /> Search sections <kbd>⌘ K</kbd></button></header>
      <main id="admin-main" className="admin-content">{children}</main>
    </div>
    <Dialog.Root open={palette} onOpenChange={setPalette}><Dialog.Portal><Dialog.Overlay className="admin-dialog-overlay" /><Dialog.Content className="admin-dialog admin-palette"><Dialog.Title>Go to section</Dialog.Title><Dialog.Description>Choose an admin section.</Dialog.Description><div className="admin-palette-list">{nav.map(item => <Link key={item.href} href={item.href} onClick={() => setPalette(false)}><item.icon size={17} aria-hidden="true" />{item.label}</Link>)}</div><Dialog.Close className="admin-dialog-close" aria-label="Close"><X size={18} /></Dialog.Close></Dialog.Content></Dialog.Portal></Dialog.Root>
  </div>;
}
