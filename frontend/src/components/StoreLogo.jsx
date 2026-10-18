import { useState } from "react";

// The store's logo (branding `logo_url`). If the image can't load (no logo
// set and an older server without /branding/logo.png), a square with the
// store's first letter in its brand colour stands in.
export default function StoreLogo({ store, size, className }) {
  const [failed, setFailed] = useState(false);
  const style = size ? { width: size, height: size } : undefined;
  if (failed || !store?.logoUrl) {
    return (
      <span
        className={`store-logo-fallback${className ? ` ${className}` : ""}`}
        style={style}
        aria-hidden="true"
        data-testid="store-logo"
      >
        {(store?.shortName || store?.name || "S").trim().charAt(0).toUpperCase()}
      </span>
    );
  }
  return (
    <img
      src={store.logoUrl}
      alt=""
      className={className}
      style={style}
      onError={() => setFailed(true)}
      data-testid="store-logo"
    />
  );
}
