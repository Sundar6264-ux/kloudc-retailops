// The product's own logged-out pages (docs/DESIGN_SYSTEM.md 2.3): sign in,
// sign-up, the request status and the owner's set-up link. Wide screens: a
// KloudC blue panel (white logo, tagline, three value points, the paper
// plane) beside the form. Phones: the logo on top, the form, then the value
// points folded under "What is KloudC RetailOps?". The footer carries
// Privacy, Support and Contact (Terms only once a terms page exists).
import { useBranding } from "../store/StoreContext.jsx";
import Icon from "../ui/icons.jsx";
import KloudcLogo from "../ui/KloudcLogo.jsx";
import { COPYRIGHT, KLOUDC, KLOUDC_LINKS, VALUE_POINTS } from "../ui/kloudc.js";

function ValuePoints() {
  return (
    <ul className="auth-points">
      {VALUE_POINTS.map((p) => (
        <li key={p.text}>
          <span className="auth-point-icon" aria-hidden="true">
            <Icon name={p.icon} size={16} />
          </span>
          {p.text}
        </li>
      ))}
    </ul>
  );
}

export function AuthFooter() {
  const brand = useBranding();
  const privacy = brand?.legal?.privacyUrl || KLOUDC_LINKS.privacy;
  const terms = brand?.legal?.termsUrl || null;
  return (
    <footer className="auth-footer">
      <nav aria-label="KloudC links">
        <a href={privacy} target="_blank" rel="noopener noreferrer">
          Privacy
        </a>
        {terms && (
          <a href={terms} target="_blank" rel="noopener noreferrer">
            Terms
          </a>
        )}
        <a href={KLOUDC_LINKS.support} target="_blank" rel="noopener noreferrer">
          Support
        </a>
        <a href={KLOUDC_LINKS.contact} target="_blank" rel="noopener noreferrer">
          Contact
        </a>
      </nav>
      <p>{COPYRIGHT}</p>
    </footer>
  );
}

export default function AuthLayout({ children, wide = false, testId }) {
  return (
    <div className="auth-page" data-testid={testId}>
      <aside className="auth-brand" aria-label={KLOUDC.product}>
        <div className="auth-brand-inner">
          <KloudcLogo tone="light" height={52} />
          <p className="auth-tagline">{KLOUDC.tagline}</p>
          <ValuePoints />
        </div>
        <span className="auth-plane" aria-hidden="true">
          <KloudcLogo mark tone="light" height={420} />
        </span>
        <p className="auth-brand-foot">
          {KLOUDC.product} by {KLOUDC.company}
        </p>
      </aside>
      <main className={`auth-main${wide ? " auth-main-wide" : ""}`}>
        <div className="auth-mobile-logo">
          <KloudcLogo height={40} />
        </div>
        <div className="auth-content">{children}</div>
        <details className="auth-what">
          <summary>What is {KLOUDC.product}?</summary>
          <p>{KLOUDC.tagline}</p>
          <ValuePoints />
        </details>
        <AuthFooter />
      </main>
    </div>
  );
}
