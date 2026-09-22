"use client";

import Link from "next/link";
import { Aperture, ArrowUpRight, Clock, LogIn, LogOut, User as UserIcon } from "lucide-react";
import type { ReactNode } from "react";
import { useUserAuth } from "./v2-auth-context";
import { V2ThemeToggle } from "./v2-theme-toggle";

export function V2Shell({ children, section = "Product research" }: { children: ReactNode; section?: string }) {
  const { user, loading, openAuthModal, logout } = useUserAuth();

  return (
    <div className="v2">
      <a className="v2-skip" href="#v2-main">Skip to main content</a>
      <header className="v2-header">
        <div className="v2-container v2-header-inner">
          <Link href="/" className="v2-brand" aria-label="ReviewLens research home">
            <span className="v2-mark"><Aperture size={19} aria-hidden="true" /></span>
            <span><strong>ReviewLens</strong><small>PRODUCT RESEARCH</small></span>
          </Link>
          <nav aria-label="Public navigation" className="v2-nav">
            <span className="v2-section-name">{section}</span>
            <Link className="v2-how-link" href="/#how-it-works">How it works <ArrowUpRight size={15} aria-hidden="true" /></Link>

            {!loading && user ? (
              <>
                <Link className="v2-nav-link v2-researches-link" href="/researches" title="View your past researches">
                  <Clock size={15} aria-hidden="true" />
                  <span>My Researches</span>
                </Link>
                <div className="v2-user-profile-menu">
                  <span className="v2-user-avatar" aria-hidden="true">
                    <UserIcon size={14} />
                  </span>
                  <span className="v2-user-email-label" title={user.email}>
                    {user.name || user.email.split("@")[0]}
                  </span>
                  <button
                    type="button"
                    className="v2-user-logout-btn"
                    onClick={logout}
                    title="Sign out"
                    aria-label="Sign out"
                  >
                    <LogOut size={13} aria-hidden="true" />
                  </button>
                </div>
              </>
            ) : !loading ? (
              <button
                type="button"
                className="v2-user-signin-btn"
                onClick={() => openAuthModal("login")}
              >
                <UserIcon size={14} aria-hidden="true" />
                <span>Sign in</span>
              </button>
            ) : null}

            <V2ThemeToggle />

            <Link className="v2-admin-link" href="/admin/login" title="Admin control plane">
              <LogIn size={14} aria-hidden="true" />
              <span>Admin</span>
            </Link>
          </nav>
        </div>
      </header>
      <main id="v2-main" className="v2-container">{children}</main>
      <footer className="v2-footer v2-container">
        <span>Evidence to help you decide. Not a substitute for hands-on testing.</span>
        <span>ReviewLens research</span>
      </footer>
    </div>
  );
}
