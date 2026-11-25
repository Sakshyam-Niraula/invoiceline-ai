import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatCurrency(amount: string | null | undefined, currency: string | null | undefined): string {
  if (!amount) return "—";
  const num = Number(amount);
  if (Number.isNaN(num)) return amount;
  const code = currency && currency.length === 3 ? currency.toUpperCase() : "USD";
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: code,
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    }).format(num);
  } catch {
    return `${code} ${num.toFixed(2)}`;
  }
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const dt = new Date(iso);
  if (Number.isNaN(dt.getTime())) return iso;
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(dt);
}

export function formatShortDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  return iso.slice(0, 10);
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}
