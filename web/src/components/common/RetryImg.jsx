import { useState } from "react";

/**
 * <img> that loads lazily (only when scrolled into view) and retries a failed load up to 6 times with a
 * growing, jittered back-off. The cloud demo's Lambda has a small concurrency limit, so an image request in
 * a page's first burst can be throttled briefly.
 */
export function RetryImg({ src, ...props }) {
  const [attempt, setAttempt] = useState(0);
  const url = attempt === 0 || !src ? src : `${src}${src.includes("?") ? "&" : "?"}_r=${attempt}`;
  return (
    <img
      loading="lazy"
      {...props}
      src={url}
      onError={(e) => {
        if (attempt < 6) setTimeout(() => setAttempt((a) => a + 1), 800 * (attempt + 1) + Math.random() * 700);
        props.onError?.(e);
      }}
    />
  );
}
