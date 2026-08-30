/**
 * Snapshot — full-viewport paper hero with Iridescence background.
 */
import Iridescence from "@/components/Iridescence";

/** #1e4593 as normalized RGB for Iridescence. */
const IRIDESCENCE_COLOR: [number, number, number] = [0.1176, 0.2706, 0.5765];

export default function Home() {
  return (
    <main className="snapshot-page">
      <header className="masthead" aria-label="Snapshot">
        <a className="brand" href="#top" aria-label="Snapshot home">
          <img src="/brand/snapshot-mark.svg" width={24} height={24} alt="" />
          <span>SNAPSHOT</span>
        </a>
        <a className="dashboard-link" href="/dashboard">
          <span>Open dashboard</span>
          <b aria-hidden="true">↗</b>
        </a>
      </header>

      <section className="hero" id="top" aria-labelledby="hero-title">
        <div className="hero-bg" aria-hidden="true">
          <Iridescence color={IRIDESCENCE_COLOR} speed={0.7} amplitude={0.1} mouseReact />
        </div>

        <div className="hero-copy">
          <p className="eyebrow">Research papers</p>
          <h1 id="hero-title">
            See what
            <br />
            actually
            <br />
            reproduces
          </h1>
          <p className="hero-dek">
            Snapshot freezes a paper’s claims and runs them as experiments so you can keep what
            holds.
          </p>
          <div className="hero-cta">
            <a className="hero-button" href="/dashboard">
              Start a reproduction
            </a>
          </div>
        </div>
      </section>
    </main>
  );
}
