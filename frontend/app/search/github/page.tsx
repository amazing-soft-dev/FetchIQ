import { SearchForm } from "../../../components/SearchForm";
import { loadGeo, loadPresets } from "../../../lib/server-api";

export default async function GitHubSearchPage() {
  // Fetched here so the form paints with its options; the client refetches if either is missing.
  const [geo, presets] = await Promise.all([loadGeo(), loadPresets()]);
  return <SearchForm initialGeo={geo} initialPresets={presets} />;
}
