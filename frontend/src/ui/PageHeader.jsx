// The page header (docs/DESIGN_SYSTEM.md 2.4):
//
//   [● Area]
//   Title ⓘ                                  [secondary] [Primary]
//   One-line subtitle (muted)
//   [tabs / tile tabs]
//
// `pageId` drives the title, the area chip and the ⓘ (help registry, else
// featureRoutes PAGE_META); `title` / `area` override. `children` render
// under the subtitle (tabs, chips, a status line).
import AreaChip from "./AreaChip.jsx";
import { InfoButton, usePageMeta } from "./helpBridge.jsx";

export default function PageHeader({
  pageId,
  title,
  subtitle,
  area,
  areaLabel,
  actions,
  children,
  info = true,
  infoExtra,
  className = "",
  titleSuffix,
  testId,
}) {
  const meta = usePageMeta(pageId);
  const shownArea = area === undefined ? meta.area : area;
  const shownTitle = title ?? meta.title;
  return (
    <header className={`page-header kc-page-header${className ? ` ${className}` : ""}`} data-area={shownArea || undefined} data-testid={testId}>
      <div className="kc-page-header-row">
        <div className="kc-page-header-main">
          {shownArea && <AreaChip area={shownArea} label={areaLabel} />}
          <div className="kc-page-title-row">
            <h1>{shownTitle}</h1>
            {titleSuffix}
            {info && pageId && <InfoButton pageId={pageId} extra={infoExtra} />}
          </div>
        </div>
        {actions && <div className="kc-page-actions">{actions}</div>}
      </div>
      {subtitle && <p className="muted kc-page-subtitle">{subtitle}</p>}
      {children}
    </header>
  );
}
