"use client";

import React from "react";
import { UserAuthProvider } from "./v2-auth-context";
import { V2AuthModal } from "./v2-auth-modal";

export function V2Providers({ children }: { children: React.ReactNode }) {
  return (
    <UserAuthProvider>
      {children}
      <V2AuthModal />
    </UserAuthProvider>
  );
}
