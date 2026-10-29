import type { Metadata } from "next";
import { Inter } from "next/font/google";
import type { ReactNode } from "react";

import { SiteHeader } from "../components/SiteHeader";
import "./globals.css";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "FetchIQ",
  description: "Search public GitHub profiles and download published contact details.",
  manifest: "/brand/site.webmanifest",
  icons: {
    icon: [
      { url: "/brand/logo-mark/logo-mark-16.png", sizes: "16x16", type: "image/png" },
      { url: "/brand/logo-mark/logo-mark-32.png", sizes: "32x32", type: "image/png" },
    ],
    apple: { url: "/brand/logo-mark/logo-mark-180.png", sizes: "180x180", type: "image/png" },
  },
  openGraph: {
    title: "FetchIQ",
    description: "Find the people behind the code.",
    images: [{ url: "/brand/logo-full/logo-full-512.png", width: 512, height: 512, alt: "FetchIQ" }],
  },
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
