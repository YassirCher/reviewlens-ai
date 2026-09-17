import "server-only";
import { notFound } from "next/navigation";
import type { Report } from "./v2";

const ORIGIN = (process.env.V2_API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

export async function loadPublicReport(token: string): Promise<Report> {
  if (!/^[A-Za-z0-9_-]{43}$/.test(token)) notFound();
  const response = await fetch(`${ORIGIN}/api/v2/reports/${token}`, { cache: "no-store", headers: { Accept: "application/json" } });
  if (response.status === 404) notFound();
  if (!response.ok) throw new Error("The report is temporarily unavailable.");
  return response.json() as Promise<Report>;
}
