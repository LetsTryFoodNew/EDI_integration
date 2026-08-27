import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { Input } from "@/components/ui/input";
import { searchMaterials } from "../api";

/**
 * Pick the SAP item a keyed-in line ships.
 *
 * A combobox rather than a free-text box: an item code typed from memory is how
 * Blinkit PO 2873410040494 reached SAP naming FG00460, which does not exist, and B1
 * rejected all eighteen lines with ODBC -2028. Choosing from the master cannot
 * produce a code that is not there.
 *
 * Search runs server-side against /master-data/materials — the master is thousands of
 * rows and is never pulled down whole.
 */
export default function ItemPicker({
  value,
  onChange,
  onPick,
}: {
  value: string;
  onChange: (code: string) => void;
  onPick?: (m: {
    item_code: string;
    item_name: string;
    hsn: string | null;
    invntry_uom: string;
  }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(term), 250);
    return () => clearTimeout(t);
  }, [term]);

  // Clicking a row inside the list must not count as clicking away, so this listens
  // on mousedown and checks containment rather than using blur.
  useEffect(() => {
    function onDown(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, []);

  const { data: options, isFetching } = useQuery({
    queryKey: ["materials", debounced],
    queryFn: () => searchMaterials(debounced),
    enabled: open,
    staleTime: 60_000,
  });

  return (
    <div ref={boxRef} className="relative">
      <div className="flex items-center gap-1">
        <Input
          value={open ? term : value}
          placeholder="Search item…"
          className="h-8 font-mono text-xs"
          onFocus={() => {
            setTerm(value);
            setOpen(true);
          }}
          onChange={(e) => {
            setTerm(e.target.value);
            onChange(e.target.value);
          }}
        />
        {value && !open && (
          <button
            type="button"
            aria-label="Clear item"
            className="text-muted-foreground hover:text-foreground shrink-0"
            onClick={() => onChange("")}
          >
            <X className="h-3.5 w-3.5" />
          </button>
        )}
      </div>

      {open && (
        <div className="absolute z-50 mt-1 w-[22rem] max-h-64 overflow-y-auto rounded-md border bg-popover shadow-md">
          {isFetching && !options ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">Searching…</p>
          ) : !options?.length ? (
            <p className="px-3 py-2 text-xs text-muted-foreground">
              {debounced
                ? `No item matches “${debounced}”.`
                : "Type to search the material master."}
            </p>
          ) : (
            options.map((m) => (
              <button
                key={m.id}
                type="button"
                className="w-full text-left px-3 py-1.5 hover:bg-accent flex items-start gap-2"
                onClick={() => {
                  onChange(m.item_code);
                  onPick?.(m);
                  setOpen(false);
                }}
              >
                {m.item_code === value ? (
                  <Check className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                ) : (
                  <span className="w-3.5 shrink-0" />
                )}
                <span className="min-w-0">
                  <span className="font-mono text-xs">{m.item_code}</span>
                  <span className="block text-xs text-muted-foreground truncate">
                    {m.item_name}
                  </span>
                </span>
              </button>
            ))
          )}
        </div>
      )}
    </div>
  );
}
