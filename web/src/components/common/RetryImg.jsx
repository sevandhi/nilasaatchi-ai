import { useState } from "react";

/**
 * <img> that retries a failed load up to 3 times with a short back-off. The cloud demo's Lambda has
 * a small concurrency limit, so an image request in a page's first burst can be throttled briefly.
 */
export function RetryImg({ src, ...props }) {
  const [attempt, setAttempt] = useState(0);
  const url = attempt === 0 || !src ? src : `${src}${src.includes("?") ? "&" : "?"}_r=${attempt}`;
  return (
    <img
      {...props}
      src={url}
      onError={(e) => {
        if (attempt < 3) setTimeout(() => setAttempt((a) => a + 1), 500 * (attempt + 1));
        props.onError?.(e);
      }}
    />
  );
}
