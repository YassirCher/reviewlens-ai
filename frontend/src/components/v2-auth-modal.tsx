"use client";

import React, { useEffect, useState } from "react";
import { AlertCircle, CheckCircle, Eye, EyeOff, Loader2, Lock, Mail, User as UserIcon, X } from "lucide-react";
import { useUserAuth } from "./v2-auth-context";

interface V2AuthModalDialogProps {
  initialTab: "login" | "register";
  closeAuthModal: () => void;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name?: string) => Promise<void>;
}

function V2AuthModalDialog({ initialTab, closeAuthModal, login, register }: V2AuthModalDialogProps) {
  const [tab, setTab] = useState<"login" | "register">(initialTab);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        closeAuthModal();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [closeAuthModal]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccess(null);
    setSubmitting(true);

    try {
      if (tab === "login") {
        await login(email, password);
      } else {
        await register(email, password, name);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Authentication failed. Please try again.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="v2-modal-overlay" onClick={closeAuthModal} role="dialog" aria-modal="true" aria-labelledby="v2-auth-title">
      <div className="v2-modal-dialog" onClick={(e) => e.stopPropagation()}>
        <button
          className="v2-modal-close"
          onClick={closeAuthModal}
          aria-label="Close dialog"
        >
          <X size={18} aria-hidden="true" />
        </button>

        <div className="v2-modal-header">
          <h2 id="v2-auth-title" className="v2-modal-title">
            {tab === "login" ? "Sign in to ReviewLens" : "Create your account"}
          </h2>
          <p className="v2-modal-desc">
            {tab === "login"
              ? "Access your research history, dossiers, and saved product evaluations."
              : "Track every product research you perform and keep past reports organized."}
          </p>
        </div>

        <div className="v2-modal-tabs">
          <button
            type="button"
            className={`v2-modal-tab ${tab === "login" ? "active" : ""}`}
            onClick={() => { setTab("login"); setError(null); }}
          >
            Sign In
          </button>
          <button
            type="button"
            className={`v2-modal-tab ${tab === "register" ? "active" : ""}`}
            onClick={() => { setTab("register"); setError(null); }}
          >
            Create Account
          </button>
        </div>

        {error && (
          <div className="v2-modal-alert error" role="alert">
            <AlertCircle size={16} aria-hidden="true" />
            <span>{error}</span>
          </div>
        )}

        {success && (
          <div className="v2-modal-alert success" role="status">
            <CheckCircle size={16} aria-hidden="true" />
            <span>{success}</span>
          </div>
        )}

        <form onSubmit={handleSubmit} className="v2-modal-form">
          {tab === "register" && (
            <div className="v2-form-group">
              <label htmlFor="auth-name">Full Name (Optional)</label>
              <div className="v2-input-wrapper">
                <UserIcon size={16} className="v2-input-icon" aria-hidden="true" />
                <input
                  id="auth-name"
                  type="text"
                  placeholder="e.g. Alex Morgan"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  autoComplete="name"
                />
              </div>
            </div>
          )}

          <div className="v2-form-group">
            <label htmlFor="auth-email">Email Address</label>
            <div className="v2-input-wrapper">
              <Mail size={16} className="v2-input-icon" aria-hidden="true" />
              <input
                id="auth-email"
                type="email"
                required
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="email"
              />
            </div>
          </div>

          <div className="v2-form-group">
            <label htmlFor="auth-password">Password</label>
            <div className="v2-input-wrapper">
              <Lock size={16} className="v2-input-icon" aria-hidden="true" />
              <input
                id="auth-password"
                type={showPassword ? "text" : "password"}
                required
                minLength={8}
                placeholder="At least 8 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete={tab === "login" ? "current-password" : "new-password"}
              />
              <button
                type="button"
                className="v2-password-toggle"
                onClick={() => setShowPassword(!showPassword)}
                aria-label={showPassword ? "Hide password" : "Show password"}
              >
                {showPassword ? <EyeOff size={16} aria-hidden="true" /> : <Eye size={16} aria-hidden="true" />}
              </button>
            </div>
          </div>

          <button
            type="submit"
            className="v2-modal-submit"
            disabled={submitting}
          >
            {submitting ? (
              <>
                <Loader2 size={16} className="v2-spin" aria-hidden="true" />
                <span>{tab === "login" ? "Signing in..." : "Creating account..."}</span>
              </>
            ) : (
              <span>{tab === "login" ? "Sign In" : "Create Account"}</span>
            )}
          </button>
        </form>

        <div className="v2-modal-footer-hint">
          {tab === "login" ? (
            <p>
              Don&apos;t have an account?{" "}
              <button
                type="button"
                className="v2-text-btn"
                onClick={() => { setTab("register"); setError(null); }}
              >
                Sign up free
              </button>
            </p>
          ) : (
            <p>
              Already have an account?{" "}
              <button
                type="button"
                className="v2-text-btn"
                onClick={() => { setTab("login"); setError(null); }}
              >
                Sign in
              </button>
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

export function V2AuthModal() {
  const { isAuthModalOpen, authModalTab, closeAuthModal, login, register } = useUserAuth();

  if (!isAuthModalOpen) return null;

  return (
    <V2AuthModalDialog
      key={authModalTab}
      initialTab={authModalTab}
      closeAuthModal={closeAuthModal}
      login={login}
      register={register}
    />
  );
}
