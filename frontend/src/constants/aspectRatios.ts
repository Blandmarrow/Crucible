// Shared aspect-ratio presets for crop UIs (ImageDetailPage crop tool,
// CropToDetectionForm). "Free" / no snap is represented by the absence of a
// value, not an entry here.
export const ASPECT_PRESETS: { label: string; value: number }[] = [
  { label: "1:1", value: 1 },
  { label: "4:3", value: 4 / 3 },
  { label: "16:9", value: 16 / 9 },
  { label: "3:2", value: 3 / 2 },
  { label: "9:16", value: 9 / 16 },
];
