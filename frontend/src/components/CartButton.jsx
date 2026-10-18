import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getCart } from "../api";

// Fixed top-right entry point to the Purchase Order Cart, present on every
// page (rendered once in App.jsx outside <Routes>, same mount-once pattern
// as ChatWidget/Sidebar) - deliberately NOT a sidebar nav item, since it's
// a persistent cross-page action rather than a page you navigate between.
export default function CartButton() {
  const navigate = useNavigate();
  const [count, setCount] = useState(0);

  function refresh() {
    getCart()
      .then((res) => setCount(res.total_items))
      .catch(() => {});
  }

  useEffect(() => {
    refresh();
    window.addEventListener("cart:updated", refresh);
    return () => window.removeEventListener("cart:updated", refresh);
  }, []);

  return (
    <button
      type="button"
      className="cart-button"
      onClick={() => navigate("/purchase-order")}
      aria-label={`Purchase order cart, ${count} item${count === 1 ? "" : "s"}`}
    >
      <CartIcon />
      {count > 0 && <span className="cart-button-badge">{count}</span>}
    </button>
  );
}

// A plain inline SVG instead of a 🛒 emoji glyph - emoji rendering depends on
// the OS/browser having that specific glyph in its color-emoji font, and it
// wasn't showing up at all on a real system this was checked on. An SVG
// path draws identically everywhere and picks up `currentColor`, so it
// always matches the button's white text color.
function CartIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="9" cy="21" r="1" fill="currentColor" stroke="none" />
      <circle cx="19" cy="21" r="1" fill="currentColor" stroke="none" />
      <path d="M2.5 3h2l2.4 12.4a2 2 0 0 0 2 1.6h8.2a2 2 0 0 0 2-1.6L21 8H6" />
    </svg>
  );
}
