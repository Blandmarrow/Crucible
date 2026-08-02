import { create } from "zustand";
import {
  GALLERY_CHECKBOX_SIZE_KEY,
  GALLERY_LICENSE_BADGE_KEY,
  GALLERY_STYLE_METER_KEY,
  clampGalleryCheckboxSize,
  getGalleryCheckboxSize,
  getGalleryLicenseBadge,
  getGalleryStyleMeter,
} from "../constants/storage";

/**
 * Live UI preferences that must reach already-mounted components.
 *
 * These are localStorage-backed like the other preference keys, but reading
 * localStorage at render time is not enough: split-view panes can hold
 * SettingsPage and GalleryPage at the same time, so a change made in Settings
 * has to re-render the gallery cards immediately rather than on next mount.
 *
 * Keys live in constants/storage.ts (single registry); this store owns the
 * write-back so components never touch localStorage for these directly.
 */
interface UiPrefsStore {
  /** Gallery selection checkbox edge length, in px. */
  galleryCheckboxSize: number;
  setGalleryCheckboxSize: (size: number) => void;
  /** Whether gallery cards show each image's effective license. */
  galleryLicenseBadge: boolean;
  setGalleryLicenseBadge: (on: boolean) => void;
  /** Whether gallery cards show the style-match percentile meter. On by default.
   *  Also gates the request behind it — `useStyleDistribution` takes this as its
   *  `enabled`, so switching it off stops the fetch, not just the render. */
  galleryStyleMeter: boolean;
  setGalleryStyleMeter: (on: boolean) => void;
}

export const useUiPrefsStore = create<UiPrefsStore>((set) => ({
  galleryCheckboxSize: getGalleryCheckboxSize(),
  setGalleryCheckboxSize: (size) => {
    const clamped = clampGalleryCheckboxSize(size);
    localStorage.setItem(GALLERY_CHECKBOX_SIZE_KEY, String(clamped));
    set({ galleryCheckboxSize: clamped });
  },
  galleryLicenseBadge: getGalleryLicenseBadge(),
  setGalleryLicenseBadge: (on) => {
    localStorage.setItem(GALLERY_LICENSE_BADGE_KEY, String(on));
    set({ galleryLicenseBadge: on });
  },
  galleryStyleMeter: getGalleryStyleMeter(),
  setGalleryStyleMeter: (on) => {
    localStorage.setItem(GALLERY_STYLE_METER_KEY, String(on));
    set({ galleryStyleMeter: on });
  },
}));
