import { AllCommunityModule, ModuleRegistry, themeQuartz } from "ag-grid-community";

// AG Grid v33+: features are modules; register the full Community bundle once at startup.
ModuleRegistry.registerModules([AllCommunityModule]);

/** Shared Theming-API theme so every grid in the Command Center looks like one product. */
export const gridTheme = themeQuartz.withParams({
  accentColor: "#0070E0",
  backgroundColor: "#FFFFFF",
  foregroundColor: "#0F172A",
  borderColor: "#E2E8F0",
  chromeBackgroundColor: "#F8FAFC",
  headerBackgroundColor: "#F8FAFC",
  headerTextColor: "#334155",
  headerFontWeight: 600,
  rowHoverColor: "#EFF6FF",
  selectedRowBackgroundColor: "#DBEAFE",
  oddRowBackgroundColor: "#FCFDFE",
  fontFamily: "inherit",
  fontSize: 13,
  headerFontSize: 12,
  spacing: 6,
  wrapperBorderRadius: 12,
  borderRadius: 8,
});

export function csvFileName(prefix: string): string {
  const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
  return `catalogpay-${prefix}-${stamp}.csv`;
}
