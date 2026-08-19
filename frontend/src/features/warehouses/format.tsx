// Formatting helpers shared by the Branch and Warehouse tables. Kept out of
// components/shared.tsx so that file exports components only — mixing the two breaks
// React Fast Refresh (oxlint react/only-export-components).

export const dash = (v: string | number | null | undefined) =>
  v === null || v === undefined || v === "" ? (
    <span className="text-muted-foreground">—</span>
  ) : (
    v
  );

/** Street / block / city / state / zip collapsed onto one line, blanks dropped. */
export function addressLine(r: {
  street: string | null;
  block: string | null;
  city: string | null;
  state: string | null;
  zip_code: string | null;
}): string | null {
  const parts = [r.street, r.block, r.city, r.state, r.zip_code].filter(Boolean);
  return parts.length ? parts.join(", ") : null;
}
