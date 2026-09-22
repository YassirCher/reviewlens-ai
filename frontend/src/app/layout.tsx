import type { Metadata } from "next";
import "./globals.css";
import "./v2.css";

import { V2Providers } from "@/components/v2-providers";

export const metadata: Metadata = {
  title: "ReviewLens — Product intelligence from real reviews",
  description: "Analyze the top YouTube product reviews into an evidence-backed buying verdict.",
};

const themeInitScript = `(function(){try{var s=localStorage.getItem('reviewlens-theme');var t=s==='light'||s==='dark'?s:(window.matchMedia('(prefers-color-scheme:light)').matches?'light':'dark');document.documentElement.setAttribute('data-theme',t);document.documentElement.style.colorScheme=t;}catch(e){document.documentElement.setAttribute('data-theme','dark');}})();`;

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <V2Providers>{children}</V2Providers>
      </body>
    </html>
  );
}
