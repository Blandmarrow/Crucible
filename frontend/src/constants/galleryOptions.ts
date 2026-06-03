export const SORT_OPTIONS = [
  { label: "Newest first",       sort: "created_at",             order: "desc" },
  { label: "Oldest first",       sort: "created_at",             order: "asc"  },
  { label: "Aesthetic ↓",         sort: "aesthetic_score",        order: "desc" },
  { label: "Aesthetic ↑",         sort: "aesthetic_score",        order: "asc"  },
  { label: "Name A-Z",           sort: "filename",               order: "asc"  },
  { label: "Style similarity ↓",  sort: "style_similarity_score", order: "desc" },
  { label: "Colorfulness ↓",      sort: "color_score",            order: "desc" },
  { label: "Custom order",       sort: "sort_order",             order: "asc"  },
] as const;
