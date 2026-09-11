export function filterModelNames(models: string[], query: string): string[] {
  const terms = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return models;
  return models.filter((model) => {
    const name = model.toLocaleLowerCase();
    return terms.every((term) => name.includes(term));
  });
}
