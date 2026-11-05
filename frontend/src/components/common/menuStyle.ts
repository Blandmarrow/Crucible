import type { CSSProperties } from "react";

/** The panel style both menus share: `ContextMenu` adds its fixed position, `MenuButton`
 *  its anchored one. Its own module because a component file that also exports a
 *  constant trips `react-refresh/only-export-components`. */
export const MENU_PANEL_STYLE: CSSProperties = {
  background: "var(--surface-2)", border: "1px solid var(--line-2)",
  borderRadius: "var(--r)", boxShadow: "0 8px 24px rgba(0,0,0,.4)",
  minWidth: 180, padding: "4px 0",
};
