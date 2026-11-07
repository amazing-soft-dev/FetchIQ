import { redirect } from "next/navigation";

// The GitHub search is the default. Each search kind has its own page under /search.
export default function HomePage() {
  redirect("/search/github");
}
