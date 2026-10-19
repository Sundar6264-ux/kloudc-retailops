// The KloudC RetailOps lockup (paper plane + "KloudC" + "RetailOps") for the
// pages that belong to the product rather than to a store: sign-in before a
// store is chosen, sign-up, the owner's set-up link and the master console.
// Built from the brand sources by backend/scripts/branding/make_brand_art.py.
export const LOCKUP_SRC = "/kloudc-retailops-logo.png";

export default function ProductLockup({ height = 44, className = "" }) {
  return (
    <img
      src={LOCKUP_SRC}
      alt="KloudC RetailOps"
      height={height}
      width={Math.round((height * 600) / 210)}
      className={`product-lockup${className ? ` ${className}` : ""}`}
      data-testid="product-lockup"
    />
  );
}
