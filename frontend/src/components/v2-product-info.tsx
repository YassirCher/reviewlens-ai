import type { ProductEvidence, ProductInfo, SampleUsed } from "@/lib/v2";

const VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

function evidenceUrl(evidence: ProductEvidence): string | null {
  if (!VIDEO_ID.test(evidence.video_id)) return null;
  const time = evidence.timestamp_seconds;
  return `https://www.youtube.com/watch?v=${evidence.video_id}${time != null && Number.isFinite(time) && time >= 0 ? `&t=${Math.floor(time)}s` : ""}`;
}

function EvidenceLinks({ evidence }: { evidence: ProductEvidence[] }) {
  return <span className="v2-product-evidence">{evidence.map((item, index) => {
    const url = evidenceUrl(item);
    return url ? <a key={`${item.video_id}-${item.source_part}-${index}`} href={url} target="_blank" rel="noopener noreferrer" title={item.excerpt}>
      Review source {index + 1}{item.source_part === "transcript" && item.timestamp_seconds != null ? ` · ${Math.floor(item.timestamp_seconds / 60)}:${String(Math.floor(item.timestamp_seconds % 60)).padStart(2, "0")}` : ` · ${item.source_part}`}
      <span className="v2-sr-only">, opens YouTube in a new tab</span>
    </a> : null;
  })}</span>;
}

export function ProductInformationCard({ info, live = false }: { info?: ProductInfo | null; live?: boolean }) {
  if (!info || (!info.facts.length && !info.variants.length)) return null;
  const groups = new Map<string, ProductInfo["facts"]>();
  const dimensions = new Map<string, ProductInfo["variants"]>();
  for (const fact of info.facts) groups.set(fact.group, [...(groups.get(fact.group) || []), fact]);
  for (const option of info.variants) dimensions.set(option.dimension, [...(dimensions.get(option.dimension) || []), option]);
  return <section className="v2-product-card" aria-labelledby={live ? "v2-live-product-heading" : "v2-report-product-heading"}>
    <div className="v2-product-card-heading"><div><p className="v2-eyebrow">PRODUCT REFERENCE</p><h2 id={live ? "v2-live-product-heading" : "v2-report-product-heading"}>Product details from reviews</h2></div>{live && <span className="v2-product-live">Updating during research</span>}</div>
    <p className="v2-product-coverage">{info.coverage_note}</p>
    <div className="v2-product-groups">{[...groups].map(([group, facts]) => <div className="v2-product-group" key={group}><h3>{group}</h3><dl>{facts.map((fact, index) => <div key={`${fact.label}-${fact.value}-${index}`} className="v2-product-detail"><dt>{fact.label}</dt><dd><strong>{fact.value}</strong>{fact.scope && <span className="v2-product-scope">{fact.scope}</span>}{fact.conflicting && <span className="v2-product-conflict">Conflicting review statements</span>}<EvidenceLinks evidence={fact.evidence} /></dd></div>)}</dl></div>)}</div>
    {dimensions.size > 0 && <div className="v2-product-variants"><h3>Options mentioned in reviews</h3><p>Listed options do not imply every combination or current availability.</p>{[...dimensions].map(([dimension, options]) => <div className="v2-product-variant-row" key={dimension}><strong>{dimension}</strong><ul>{options.map((option, index) => <li key={`${option.value}-${index}`}><span>{option.value}{option.scope && <small> · {option.scope}</small>}</span><EvidenceLinks evidence={option.evidence} /></li>)}</ul></div>)}</div>}
  </section>;
}

export function SampleUsedBlock({ sample }: { sample?: SampleUsed | null }) {
  if (sample == null) return null;
  return <section className="v2-sample-used" aria-label="Reviewer sample used"><h4>Sample used in this review</h4>{sample.units.length === 0 ? <p>Unconfirmed</p> : <>{sample.units.map((unit, unitIndex) => <div key={`${unit.role}-${unitIndex}`}><strong>{unit.role}</strong><ul>{unit.details.map((detail, index) => <li key={`${detail.label}-${index}`}><span>{detail.label}: <strong>{detail.value}</strong></span><EvidenceLinks evidence={[detail.evidence]} /></li>)}</ul></div>)}<p>Other sample details: Unconfirmed</p></>}</section>;
}
