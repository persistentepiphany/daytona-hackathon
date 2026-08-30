/**
 * React binding for the run feed. The stream can burst hundreds of frames during a
 * replay, so frames are buffered and folded into state on a fixed cadence rather than
 * once per event — one render per flush instead of one per log line.
 */
import { useEffect, useRef, useState } from "react";
import {
  emptyFeedState,
  reduceFrames,
  subscribeRunFeed,
  type EstimateFrame,
  type FeedFrame,
  type FeedState,
  type FeedStatus,
} from "@/lib/feed";

const FLUSH_MS = 200;

export type UseRunFeed = {
  feed: FeedState;
  status: FeedStatus;
};

export function useRunFeed(
  jobId: string | null,
  options: { enabled?: boolean; replay?: "paced" | null; speed?: number } = {},
): UseRunFeed {
  const { enabled = true, replay = null, speed } = options;
  const [feed, setFeed] = useState<FeedState>(emptyFeedState);
  const [status, setStatus] = useState<FeedStatus>("idle");
  const pending = useRef<FeedFrame[]>([]);
  const estimate = useRef<EstimateFrame | null>(null);

  useEffect(() => {
    pending.current = [];
    estimate.current = null;
    setFeed(emptyFeedState());
    if (!jobId || !enabled) {
      setStatus("idle");
      return;
    }
    setStatus("waiting");

    const subscription = subscribeRunFeed(jobId, {
      onFrames: (frames) => pending.current.push(...frames),
      onEstimate: (next) => {
        estimate.current = next;
      },
      onStatus: setStatus,
      replay,
      speed,
    });

    const flush = window.setInterval(() => {
      const frames = pending.current;
      const nextEstimate = estimate.current;
      if (!frames.length && !nextEstimate) return;
      pending.current = [];
      estimate.current = null;
      setFeed((current) => {
        const folded = reduceFrames(current, frames);
        if (!nextEstimate) return folded;
        return { ...folded, estimate: nextEstimate };
      });
    }, FLUSH_MS);

    return () => {
      subscription.close();
      window.clearInterval(flush);
    };
  }, [jobId, enabled, replay, speed]);

  return { feed, status };
}
