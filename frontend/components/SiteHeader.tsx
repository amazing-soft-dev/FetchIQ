"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Search, Users } from "lucide-react";

import { cn } from "../lib/utils";

const links = [
  { href: "/", label: "Search", icon: Search },
  { href: "/contacts", label: "Contacts", icon: Users },
];

export function SiteHeader() {
  const pathname = usePathname();

  return (
    <header className="sticky top-0 z-30 flex h-12 items-center justify-between border-b border-line bg-header px-4">
      <Link href="/" className="text-sm font-semibold tracking-tight text-ink">
        FetchIQ
      </Link>
      <nav className="flex items-center gap-1">
        {links.map((link) => {
          const active = link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
          const Icon = link.icon;
          return (
            <Link
              key={link.href}
              href={link.href}
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-control px-2.5 text-sm text-muted hover:bg-hover hover:text-ink",
                active && "bg-white text-ink shadow-[0_1px_2px_rgba(20,20,30,0.06)]",
              )}
            >
              <Icon className="size-4" />
              {link.label}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
