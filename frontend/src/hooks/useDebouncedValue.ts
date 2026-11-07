import { useEffect, useState } from "react";

/** `value`, but only after it has stopped changing for `delay` ms. The first render
    returns `value` as given, so a consumer never starts from nothing. */
export function useDebouncedValue<T>(value: T, delay: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(t);
  }, [value, delay]);
  return debounced;
}
