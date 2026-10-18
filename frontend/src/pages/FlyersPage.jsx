import { useCallback, useEffect, useState } from "react";
import { getFlyerMeta } from "../api";
import { useAuth } from "../AuthContext.jsx";
import WhatsAppGroupsPanel from "../components/checklists/WhatsAppGroupsPanel.jsx";
import FlyerDrawer from "../components/flyers/FlyerDrawer.jsx";
import { clearFlyerImageCache } from "../components/flyers/FlyerImage.jsx";
import FlyerLibrary from "../components/flyers/FlyerLibrary.jsx";
import FlyerUploadCard from "../components/flyers/FlyerUploadCard.jsx";
import { withMetaDefaults } from "../components/flyers/flyerModel.js";
import useFlyerLibrary from "../components/flyers/useFlyerLibrary.js";

// Marketing flyers (docs/MARKETING_FLYERS_CONTRACT.md, section 6): upload a
// flyer, pick the marketing WhatsApp groups, send; plus the library of every
// flyer (stored on the Mac mini in ~/Spice Town Flyers/<Type>/). Needs the
// `marketing_flyers` feature; admins always have it.
export default function FlyersPage() {
  const { user } = useAuth();
  const isAdmin = Boolean(user?.is_admin);
  const [meta, setMeta] = useState(null);
  const [metaError, setMetaError] = useState(null);
  const [openId, setOpenId] = useState(null);
  const [managingGroups, setManagingGroups] = useState(false);
  const [notice, setNotice] = useState(null);
  const lib = useFlyerLibrary({ skipPollId: openId });

  const loadMeta = useCallback(() => {
    setMetaError(null);
    getFlyerMeta()
      .then((m) => setMeta(withMetaDefaults(m)))
      .catch((err) => setMetaError(err.message || "Couldn't load the flyer settings."));
  }, []);

  useEffect(() => {
    loadMeta();
  }, [loadMeta]);

  // Thumbnails are cached while the page is open; free them when it closes.
  useEffect(() => () => clearFlyerImageCache(), []);

  const manageGroups = useCallback(() => {
    setOpenId(null);
    setManagingGroups(true);
  }, []);

  const header = (
    <div className="page-header">
      <h1>Flyers</h1>
      <p className="muted">
        Upload a marketing flyer, pick the WhatsApp groups and send it. Every flyer stays in the library below, filed by
        type in the Spice Town Flyers folder on the Mac mini.
      </p>
    </div>
  );

  if (user?.demo) {
    return (
      <>
        {header}
        <section className="card">
          <p style={{ margin: 0 }}>Flyers aren't available on the demo account.</p>
        </section>
      </>
    );
  }

  return (
    <div className="fly-page">
      {header}

      {metaError && (
        <section className="card">
          <p className="error" role="alert" style={{ marginTop: 0 }}>
            {metaError}
          </p>
          <button type="button" className="btn-secondary fly-btn-sm" onClick={loadMeta}>
            Try again
          </button>
        </section>
      )}
      {!meta && !metaError && (
        <section className="card">
          <p className="muted" style={{ margin: 0 }}>
            Loading…
          </p>
        </section>
      )}

      {meta && (
        <>
          <FlyerUploadCard
            meta={meta}
            isAdmin={isAdmin}
            onManageGroups={manageGroups}
            onSaved={() => {
              setNotice(null);
              lib.reload();
            }}
            onOpen={setOpenId}
          />
          {notice && (
            <p className="success fly-page-notice" role="status">
              {notice}
            </p>
          )}
          <FlyerLibrary lib={lib} categories={meta.categories} onOpen={setOpenId} />
        </>
      )}

      {meta && openId != null && (
        <FlyerDrawer
          key={openId}
          flyerId={openId}
          meta={meta}
          isAdmin={isAdmin}
          onClose={() => setOpenId(null)}
          onUpdated={(flyer, opts) => {
            lib.patchItem(flyer);
            if (opts?.reload) lib.reload();
          }}
          onDeleted={(flyer) => {
            setOpenId(null);
            setNotice(`Deleted "${flyer.title}". The file is in the _Deleted folder on the Mac mini.`);
            lib.reload();
          }}
          onManageGroups={manageGroups}
        />
      )}

      {managingGroups && isAdmin && (
        <WhatsAppGroupsPanel
          onClose={() => {
            setManagingGroups(false);
            loadMeta();
          }}
          onChanged={loadMeta}
        />
      )}
    </div>
  );
}
