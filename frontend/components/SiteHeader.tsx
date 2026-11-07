"use client";

import Image from "next/image";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { ChevronDown, Search, Users } from "lucide-react";

import { cn } from "../lib/utils";
import { SearchStatusLink } from "./SearchStatusLink";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "./ui/dropdown-menu";

export const SEARCH_PAGES = [
  { href: "/search/github", label: "GitHub profiles" },
  { href: "/search/hacker-news", label: "Hacker News" },
] as const;

const itemClass =
  "inline-flex h-8 items-center gap-1.5 rounded-control px-2.5 text-sm text-muted hover:bg-hover hover:text-ink";
const activeClass = "bg-white text-ink shadow-[0_1px_2px_rgba(20,20,30,0.06)]";

export function SiteHeader() {
  const pathname = usePathname();
  const router = useRouter();
  const searching = pathname === "/" || pathname.startsWith("/search");
  const contacts = pathname.startsWith("/contacts");

  return (
    <header className="sticky top-0 z-30 flex h-12 items-center justify-between border-b border-line bg-header px-4">
      <Link href="/" className="inline-flex items-center gap-2 text-sm font-semibold tracking-tight text-ink">
        <Image src="/brand/logo-mark/logo-mark-192.png" alt="" width={24} height={24} priority />
        FetchIQ
      </Link>
      <nav className="flex items-center gap-1">
        <SearchStatusLink />
        <DropdownMenu>
          <DropdownMenuTrigger
            className={cn(itemClass, searching && activeClass)}
            aria-label="Choose a search"
          >
            <Search className="size-4" />
            Search
            <ChevronDown className="size-3.5" aria-hidden="true" />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            {SEARCH_PAGES.map((page) => (
              <DropdownMenuItem
                key={page.href}
                className={cn(pathname === page.href && "font-medium")}
                onSelect={() => router.push(page.href)}
              >
                {page.label}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        <Link href="/contacts" className={cn(itemClass, contacts && activeClass)}>
          <Users className="size-4" />
          Contacts
        </Link>
      </nav>
    </header>
  );
}
