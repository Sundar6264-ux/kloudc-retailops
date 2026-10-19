import { useCallback, useEffect, useMemo, useState } from "react";
import { getFlyerMeta } from "../api";
import { useAuth } from "../AuthContext.jsx";
import WhatsAppGroupsPanel from "../components/checklists/WhatsAppGroupsPanel.jsx";
import FlyerDrawer from "../components/flyers/FlyerDrawer.jsx";
import { clearFlyerImageCache } from "../components/flyers/FlyerImage.jsx";
import FlyerLibrary from "../components/flyers/FlyerLibrary.jsx";
import FlyerUploadCard from "../components/flyers/FlyerUploadCard.jsx";
import { withMetaDefaults } from "../components/flyers/flyerModel.js";
import useFlyerLibrary from "../components/flyers/useFlyerLibrary.js";
import { useStoreAllows } from "../store/StoreContext.jsx";
import PageHeader from "../ui/PageHeader.jsx";

// Marketing flyers (docs/MARKETING_FLYERS_CONTRACT.md, section 6): upload a
// flyer, pick the marketing WhatsApp groups, send; plus the library of every
// flyer (stored on the server in FLYERS_DIR/<Type>/). Needs the
// `marketing_flyers` feature; admins always have it.
export default function FlyersPage() {
  const { user } = useAuth();
  const isAdmin = Boolean(user?.is_admin);
  const [rawMeta, setMeta] = useState(null);
  // A store without WhatsApp keeps the library but loses every send control.
  const whatsapp = useStoreAllows("whatsapp");
  const meta = useMemo(() => (rawMeta ? { ...rawMeta, whatsapp_enabled: whatsapp } : null), [rawMeta, whatsapp]);
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
    <PageHeader pageId="flyers">
      <p className="muted">
        Upload a marketing flyer, pick the WhatsApp groups and send it. Every flyer stays in the library below, filed by
        type in the store's flyers folder on the server.
      </p>
    </PageHeader>
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
            setNotice(`Deleted "${flyer.title}". The file is in the _Deleted folder on the server.`);
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
