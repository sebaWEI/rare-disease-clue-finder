import { useCallback, useState } from "react";

export interface HpoItem {
  id: string;
  name: string;
}

// Ordered, de-duplicated selection of HPO terms with a small, stable API.
export function useHpoSelection() {
  const [items, setItems] = useState<HpoItem[]>([]);

  const has = useCallback((id: string) => items.some((it) => it.id === id), [items]);

  const toggle = useCallback((id: string, name: string) => {
    setItems((prev) =>
      prev.some((it) => it.id === id)
        ? prev.filter((it) => it.id !== id)
        : [...prev, { id, name }],
    );
  }, []);

  const remove = useCallback((id: string) => {
    setItems((prev) => prev.filter((it) => it.id !== id));
  }, []);

  const clear = useCallback(() => setItems([]), []);

  const setAll = useCallback((next: HpoItem[]) => {
    const seen = new Set<string>();
    setItems(next.filter((it) => (seen.has(it.id) ? false : (seen.add(it.id), true))));
  }, []);

  return { items, has, toggle, remove, clear, setAll };
}
