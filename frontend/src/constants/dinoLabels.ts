export const DINO_LAYER_LABELS: Record<string, string> = {
  "1":  "Low-level color & gradients",
  "2":  "Edges & corners",
  "3":  "Textures & patterns",
  "4":  "Local structures",
  "5":  "Object parts",
  "6":  "Part relationships",
  "7":  "Semantic regions",
  "8":  "Global semantics",
  "9":  "Scene composition",
  "10": "High-level context",
  "11": "Abstract semantics",
  // Not "Final representation": the layer picker now offers a separate "Final
  // embedding" option for the post-layernorm `dino_embedding` column, and two
  // options both calling themselves final would be exactly the confusion the
  // three-valued picker exists to remove. This is `hidden_states[12]`.
  "12": "Last block, pre-layernorm",
};
