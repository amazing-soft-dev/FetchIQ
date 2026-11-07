import { HackerNewsSearchForm } from "../../../components/HackerNewsSearchForm";
import { loadGeo } from "../../../lib/server-api";

export default async function HackerNewsSearchPage() {
  const geo = await loadGeo();
  return <HackerNewsSearchForm initialGeo={geo} />;
}
