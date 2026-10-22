// /#/label-printing/open (docs/LABEL_PRINTING_FEATURE.md 3.1): where the
// label site sends a signed-out visitor, and the footer's Label printing
// link. Signed in with the feature on: a one-time code, then straight to the
// label site. Feature off: the Request card. (Signed out, the normal login
// shows first and comes back here.)
import { useEffect, useState } from "react";
import { useAuth } from "../AuthContext.jsx";
import { platformErrorMessage } from "../platform/platformModel.js";
import LabelPrintingCard, { openLabelPrinting, useLabelPrinting } from "../settings/LabelPrintingCard.jsx";
import PageHeader from "../ui/PageHeader.jsx";
import "../platform/console.css";

export default function LabelPrintingOpenPage() {
  const { user } = useAuth();
  const { status } = useLabelPrinting();
  const [error, setError] = useState(null);
  const [opening, setOpening] = useState(false);

  useEffect(() => {
    if (status?.state !== "on" || opening) return;
    setOpening(true);
    openLabelPrinting().catch((err) => {
      setError(platformErrorMessage(err));
      setOpening(false);
    });
  }, [status, opening]);

  return (
    <div data-testid="label-printing-open-page">
      <PageHeader title="Label printing" />
      {error && (
        <div className="pf-banner bad" role="alert">
          {error}
        </div>
      )}
      {status === null || (status.state === "on" && !error) ? (
        <p className="muted" role="status">
          Opening label printing…
        </p>
      ) : (
        <LabelPrintingCard isAdmin={!!user?.is_admin} initial={status} />
      )}
    </div>
  );
}
