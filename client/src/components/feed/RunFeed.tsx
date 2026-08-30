/**
 * The live feed for one job, folded into the workspace's own activity chrome.
 *
 * The feed is opt-in on the run side (REPRO_TELEMETRY), and a queued job has no ledger
 * yet, so this must degrade rather than fail: until frames arrive it shows the polled
 * log tail, and it swaps to the streamed view the moment the first event lands.
 */
import { FileSearch, Grid3x3, Radio } from "lucide-react";
import Activity from "@/components/Activity";
import ActivityStream from "@/components/feed/ActivityStream";
import AttemptGrid from "@/components/feed/AttemptGrid";
import RunTimeline from "@/components/feed/RunTimeline";
import { useRunFeed } from "@/hooks/useRunFeed";
import { isFeedEmpty, type FeedStatus } from "@/lib/feed";

const STATUS_COPY: Record<FeedStatus, string> = {
  idle: "not streaming",
  waiting: "waiting for the run to start",
  live: "streaming",
  reconnecting: "reconnecting…",
  finished: "stream finished",
};

type Props = {
  jobId: string;
  /** The run is still going, so the stream is worth holding open. */
  running: boolean;
  /** Polled log tail, shown until the stream produces its first frame. */
  fallbackLog: string[];
};

export default function RunFeed({ jobId, running, fallbackLog }: Props) {
  // a finished run still replays from its ledger, so the stream stays enabled either
  // way; `running` only decides how loudly the panels open
  const { feed, status } = useRunFeed(jobId);
  const empty = isFeedEmpty(feed);
  // an open stream over a run that has not written anything yet is connected, not
  // streaming; saying "streaming" beside an empty panel reads as a bug
  const statusCopy =
    empty && status === "live" ? "connected · no events yet" : STATUS_COPY[status];

  if (empty) {
    if (!fallbackLog.length) {
      return (
        <Activity
          icon={<Radio size={15} />}
          title="Live feed"
          detail={statusCopy}
          defaultOpen={running}
        >
          <div className={`tool-detail ${running ? "tool-running" : ""}`}>
            {running && <span className="pulse-dot" />}
            <code>
              {status === "waiting"
                ? "no ledger for this run yet — the feed opens as soon as the run starts"
                : statusCopy}
            </code>
          </div>
        </Activity>
      );
    }
    return (
      <Activity
        icon={<FileSearch size={15} />}
        title="Live log"
        detail={`${fallbackLog.length} lines · ${statusCopy}`}
        defaultOpen={running}
      >
        <pre className="run-log">{fallbackLog.slice(-40).join("\n")}</pre>
      </Activity>
    );
  }

  const attemptsRunning = feed.attempts.filter((tile) => tile.state === "running").length;

  return (
    <>
      <RunTimeline gates={feed.gates} budget={feed.budget} estimate={feed.estimate} />

      <Activity
        icon={<Radio size={15} />}
        title="Live feed"
        detail={`${feed.frames} events · ${statusCopy}`}
        defaultOpen
        openKey={jobId}
      >
        <ActivityStream rows={feed.activity} />
      </Activity>

      <Activity
        icon={<Grid3x3 size={15} />}
        title="Attempts"
        detail={
          feed.attempts.length
            ? `${feed.attempts.length} attempt${feed.attempts.length === 1 ? "" : "s"}` +
              (attemptsRunning ? ` · ${attemptsRunning} running` : "")
            : "none yet"
        }
        defaultOpen={feed.attempts.length > 0}
        openKey={`${jobId}:${feed.attempts.length > 0}`}
      >
        <AttemptGrid tiles={feed.attempts} />
      </Activity>
    </>
  );
}
