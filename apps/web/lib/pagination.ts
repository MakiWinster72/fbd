export function paginate<T>(items: readonly T[], requestedPage: number, pageSize = 8) {
  const size = Math.max(1, Math.floor(pageSize) || 8);
  const pages = Math.max(1, Math.ceil(items.length / size));
  const page = Math.min(pages, Math.max(1, Math.floor(requestedPage) || 1));
  return { page, pages, items: items.slice((page - 1) * size, page * size) };
}
