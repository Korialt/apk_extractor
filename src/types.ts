export type BundleFormat = "apks" | "xapk";
export type Theme = "light" | "dark";

export interface AppEntry {
  packageName: string;
  baseApkPath: string;
  label: string | null;
  iconDataUrl: string | null;
}

export interface ScanStarted {
  deviceSerial: string;
  total: number;
}

export interface ScanItem {
  index: number;
  total: number;
  app: AppEntry;
}

export interface ScanSummary {
  total: number;
  iconCount: number;
}

export interface ExportResult {
  outputFile: string;
  packageDir: string;
  pulledFiles: string[];
  remotePaths: string[];
  note: string | null;
}
