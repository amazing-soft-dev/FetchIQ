import type { Metadata } from "next";
import { Inter } from "next/font/google";
import type { ReactNode } from "react";

import { SiteHeader } from "../components/SiteHeader";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "FetchIQ",
  description: "Search public GitHub profiles and download published contact details.",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen bg-canvas font-sans text-ink antialiased">
        <SiteHeader />
        <main className="mx-auto w-full max-w-[1440px] px-4 py-6">{children}</main>
      </body>
    </html>
  );
}
