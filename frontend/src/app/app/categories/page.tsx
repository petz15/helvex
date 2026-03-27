import { fetchTaxonomy } from "@/lib/api";
import { CategoriesClient } from "./categories-client";

export const dynamic = "force-dynamic";

export default async function CategoriesPage() {
  const taxonomy = await fetchTaxonomy().catch(() => ({
    clusters: [],
    keywords: [],
    categories: [],
    tags: [],
  }));

  return <CategoriesClient taxonomy={taxonomy} />;
}
