import React from "react";
import { CalculateMetadataFunction } from "remotion";
import { Explainer, ExplainerProps } from "./Explainer";

/**
 * SocialReel — the Remotion composition for the instagram-reels-studio pipeline
 * (renderer_family "social-reel"). Vertical 9:16 reel format.
 *
 * It reuses the proven Explainer renderer, which reads useVideoConfig() and therefore
 * adapts to the 1080x1920 frame. This is a distinct, registered composition (its own id +
 * vertical metadata) rather than a Python-side alias, so video_compose can route
 * renderer_family="social-reel" to a real composition. The timeline length is derived from
 * the cuts (max out_seconds), with a 5s floor for empty/preview props.
 */

export type SocialReelProps = ExplainerProps;

export const socialReelDefault: SocialReelProps = { cuts: [] };

export const SocialReel: React.FC<SocialReelProps> = (props) => {
  return <Explainer {...props} />;
};

export const calcSocialReelMetadata: CalculateMetadataFunction<SocialReelProps> = ({
  props,
}) => {
  const cuts = Array.isArray(props.cuts) ? props.cuts : [];
  const last = cuts.reduce(
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    (m: number, c: any) => Math.max(m, Number(c?.out_seconds) || 0),
    0,
  );
  const seconds = last > 0 ? last : 5;
  return {
    durationInFrames: Math.ceil(seconds * 30),
    width: 1080,
    height: 1920,
    fps: 30,
  };
};
