import type { Metadata } from "next";
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
          <span className="mark">FetchIQ</span>
          <span className="tagline">Public profile discovery</span>
        </header>
        <main>{children}</main>
      </body>
    </html>
  );
}
