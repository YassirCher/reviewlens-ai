import type { Metadata } from "next";
import "./globals.css";
import "./v2.css";

import { V2Providers } from "@/components/v2-providers";

export const metadata: Metadata = {
  title: "ReviewLens — Product intelligence from real reviews",
  description: "Analyze the top YouTube product reviews into an evidence-backed buying verdict.",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <V2Providers>{children}</V2Providers>
      </body>
    </html>
  );
}
