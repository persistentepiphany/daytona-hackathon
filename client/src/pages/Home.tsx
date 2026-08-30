/**
 * Snapshot / Forensic Modernism
 * This page behaves as an animated evidence ledger: Swiss-documentary typography, verdict cyan, and motion only when a claim freezes, runs, or resolves.
 */
import { useEffect, useState } from "react";

const steps = [
  {
    code: "01 / INTAKE",
    title: "Extract the claim.",
    copy: "Paper language becomes a claims table, an ambiguity ledger, and a bounded experiment set.",
  },
  {
    code: "02 / FREEZE",
    title: "Commit the conditions.",
    copy: "Snapshot fixes the tolerance, seeds, configuration, and the exact state every attempt begins from.",
  },
  {
    code: "03 / RUN",
    title: "Collect independent evidence.",
    copy: "Each scientific question runs from the same immutable snapshot with an explicit lineage.",
  },
  {
    code: "04 / VERDICT",
    title: "Keep what survives.",
    copy: "A deterministic verifier compares persisted evidence to the preregistration. The paper does not get the final word.",
  },
];

const constraints = [
  ["S₀ / immutable", "One frozen state. Every attempt starts there."],
  ["G1 / approved", "No compute before the scientific conditions are committed."],
  ["ledger / replayable", "The attempt resolves from hashes, commands, data, and seeds."],
  ["verifier / sealed", "Verdicts derive from preregistration and evidence, never authority."],
];

export default function Home() {
  const [stage, setStage] = useState(0);

  useEffect(() => {
    const interval = window.setInterval(() => {
      setStage((current) => (current + 1) % steps.length);
    }, 1450);
    return () => window.clearInterval(interval);
  }, []);

  useEffect(() => {
    const revealElements = document.querySelectorAll<HTMLElement>("[data-reveal]");
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) entry.target.classList.add("is-visible");
        });
      },
      { threshold: 0.16 },
    );
    revealElements.forEach((element) => observer.observe(element));
    return () => observer.disconnect();
  }, []);

  const activeStep = steps[stage];

  return (
    <main className={`snapshot-page sequence-stage-${stage}`}>
      <header className="masthead" aria-label="Snapshot">
        <div className="masthead-left">
          <a className="brand" href="#top" aria-label="Snapshot home">
            <img src="/manus-storage/snapshot-mark_770536c7.png" alt="" />
            <span>SNAPSHOT</span>
          </a>
          <a className="dashboard-link" href="#top">
            <span>Open dashboard</span>
            <b aria-hidden="true">↗</b>
          </a>
        </div>
      </header>

      <section className="hero" id="top" aria-labelledby="hero-title">
        <div className="hero-index" aria-hidden="true">
          <span>01</span>
          <i />
          <span>04</span>
        </div>
        <div className="hero-copy">
          <p className="eyebrow">Preregistered paper reproduction</p>
          <h1 id="hero-title">
            Published claims<br />
            are not evidence.
          </h1>
          <p className="hero-dek">
            Snapshot turns a paper into replayable experiments. Freeze the conditions. Run the counterfactuals. Build what survives.
          </p>
          <p className="source-note">A system for teams that cannot afford trust by assertion.</p>
        </div>

        <div className="hero-apparatus" aria-label="An animated preview of the Snapshot evidence process">
          <img
            className="hero-art"
            src="/manus-storage/snapshot-hero-freeze_6b04be5e.jpg"
            alt="An abstract paper sheet held in a registration frame with branching cyan evidence traces."
          />
          <div className="apparatus-shade" />
          <div className="protocol-card">
            <div className="protocol-meta"><span>LIVE PROTOCOL</span><span>00:{String(stage + 1).padStart(2, "0")}</span></div>
            <p className="protocol-code">{activeStep.code}</p>
            <p className="protocol-title">{activeStep.title}</p>
            <div className="protocol-track" aria-hidden="true"><i /><i /><i /><i /></div>
          </div>
          <span className="scan-mark scan-mark-a" />
          <span className="scan-mark scan-mark-b" />
        </div>

        <div className="hero-lineage" aria-hidden="true">
          <span className="lineage-label">CLAIM</span>
          <svg viewBox="0 0 800 160" preserveAspectRatio="none">
            <path className="lineage-ghost" d="M0 80 H218 C270 80 283 40 333 40 H467 C517 40 530 120 582 120 H800" />
            <path className="lineage-active" d="M0 80 H218 C270 80 283 40 333 40 H467 C517 40 530 120 582 120 H800" />
          </svg>
          <span className="lineage-label">VERDICT</span>
        </div>
      </section>

      <section className="thesis-band" data-reveal>
        <p className="thesis-index">THE PREMISE / 00</p>
        <p className="thesis-copy">
          A result is credible only when its conditions are <em>frozen</em>, its attempts are <em>traceable</em>, and its verdict can be <em>re-derived</em>.
        </p>
      </section>

      <section className="sequence-section" aria-labelledby="sequence-title">
        <div className="section-header" data-reveal>
          <p className="eyebrow">The route from claim to build</p>
          <h2 id="sequence-title">One paper.<br />Four controlled states.</h2>
        </div>
        <div className="sequence-list">
          {steps.map((item, index) => (
            <article className={`sequence-row ${index === stage ? "is-current" : ""}`} key={item.code} data-reveal>
              <span className="sequence-count">0{index + 1}</span>
              <div className="sequence-rule" aria-hidden="true"><i /></div>
              <div className="sequence-main">
                <p className="sequence-code">{item.code}</p>
                <h3>{item.title}</h3>
              </div>
              <p className="sequence-detail">{item.copy}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="freeze-section" aria-labelledby="freeze-title">
        <div className="freeze-image-wrap" data-reveal>
          <img src="/manus-storage/snapshot-parallel-evidence_ea1e2e4d.jpg" alt="Abstract evidence threads branching through separate execution cells and reconverging at a verdict gate." />
          <span className="image-caption">Attempt lineage / independent paths / one record</span>
        </div>
        <div className="freeze-copy" data-reveal>
          <p className="eyebrow">The anchor</p>
          <h2 id="freeze-title">No mutable<br />starting point.</h2>
          <p>
            S₀ is a frozen environment—not a remembered setup. Experiments spawn directly from it, then prove their dataset and manifest before they spend a single step of compute.
          </p>
          <div className="freeze-hash">
            <span>S₀ SHA-256</span>
            <b>9D70…B6A1</b>
            <i>LOCKED</i>
          </div>
        </div>
      </section>

      <section className="constraints-section" aria-labelledby="constraints-title">
        <div className="constraints-heading" data-reveal>
          <p className="eyebrow">The controls</p>
          <h2 id="constraints-title">Trust has<br />a constraint set.</h2>
        </div>
        <div className="constraints-list">
          {constraints.map(([label, detail], index) => (
            <article className="constraint" key={label} data-reveal>
              <span>0{index + 1}</span>
              <div><h3>{label}</h3><p>{detail}</p></div>
            </article>
          ))}
        </div>
        <img className="paper-detail" src="/manus-storage/snapshot-paper-detail_a23dd3e1.jpg" alt="A close crop of a paper registration mark and cyan line." />
      </section>

      <section className="verdict-section" aria-labelledby="verdict-title">
        <div className="verdict-visual" data-reveal>
          <img src="/manus-storage/snapshot-verdict-gate_8174b8d8.jpg" alt="An abstract mechanical threshold with a cyan verification beam passing through it." />
          <p>VERIFIER / INPUTS ONLY: PREREG + EVIDENCE</p>
        </div>
        <div className="verdict-copy" data-reveal>
          <p className="eyebrow">The verdict</p>
          <h2 id="verdict-title">Build what<br />survived.</h2>
          <p>
            Snapshot does not grade a paper. It compares evidence to the conditions agreed before the run—then names exactly what reproduced, what failed, and what remains under-constrained.
          </p>
          <div className="verdict-stamp"><span>VERDICT</span><strong>Evidence<br />over authority</strong></div>
        </div>
      </section>

      <footer className="footer">
        <span>SNAPSHOT</span>
        <span>REPRODUCTION / AS INFRASTRUCTURE</span>
        <span>© 2026</span>
      </footer>
    </main>
  );
}
