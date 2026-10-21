import type { Metadata } from "next";
import Link from "next/link";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: "FetchIQ",
  description: "Search public GitHub profiles and download published contact details.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en">
      <body>
        <header className="site-header">
          <Link href="/" className="mark">
            FetchIQ
          </Link>
          <nav className="site-nav">
            <Link href="/">Search</Link>
            <Link href="/contacts">Contacts</Link>
          </nav>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
