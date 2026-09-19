import type { ReactNode } from "react";
import { AdminShell } from "@/components/admin-shell";
import "./admin.css";
export const metadata = { title: "Admin | ReviewLens", robots: { index: false, follow: false } };
export default function Layout({ children }: { children: ReactNode }) { return <AdminShell>{children}</AdminShell>; }
