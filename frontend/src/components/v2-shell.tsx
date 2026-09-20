import Link from "next/link";
import { Aperture, ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";

export function V2Shell({ children, section = "Product research" }: { children: ReactNode; section?: string }) {
  return <div className="v2">
    <a className="v2-skip" href="#v2-main">Skip to main content</a>
    <header className="v2-header">
      <div className="v2-container v2-header-inner">
        <Link href="/" className="v2-brand" aria-label="ReviewLens research home"><span className="v2-mark"><Aperture size={19} aria-hidden="true" /></span><span><strong>ReviewLens</strong><small>PRODUCT RESEARCH</small></span></Link>
        <nav aria-label="Public navigation" className="v2-nav"><span className="v2-section-name">{section}</span><Link href="/#how-it-works">How it works <ArrowUpRight size={15} aria-hidden="true" /></Link></nav>
      </div>
    </header>
    <main id="v2-main" className="v2-container">{children}</main>
    <footer className="v2-footer v2-container"><span>Evidence to help you decide. Not a substitute for hands-on testing.</span><span>ReviewLens research</span></footer>
  </div>;
}
