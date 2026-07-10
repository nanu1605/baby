/**
 * Post-processing bloom (V3c) — the glow that makes the neural sphere read as
 * "alive". This is the #1 VRAM item, so it renders ONLY at the full3d tier (the
 * governor sheds it first on demote — see Scene's `plan.bloom` gate).
 *
 * Washout fix (owner GPU checkpoint): <EffectComposer> takes over rendering, which
 * drops three's built-in ACES tone mapping — without re-applying it the scene goes
 * flat, low-contrast, "washed out, no glow". So:
 *   1. re-apply ACES tone mapping as the last effect (restores contrast),
 *   2. raise the luminance threshold so ONLY the bright emissive node cores bloom
 *      (0.2 bloomed the whole lit sphere → a grey haze, not selective glow),
 *   3. Scene pushes node emissive into HDR so those cores punch above the threshold.
 * Result: crisp glowing cores over a dark sphere instead of a uniform wash.
 */
import { Bloom, EffectComposer, ToneMapping } from "@react-three/postprocessing";
import { ToneMappingMode } from "postprocessing";

export default function Effects() {
  return (
    // multisampling 0: the default 8x MSAA half-float input buffer is the composer's
    // dominant VRAM cost (~0.5 GB at typical sizes) and is largely redundant under
    // mipmap-blur bloom. Keeping the footprint ~0.15 GB also keeps it safely inside
    // the watchdog's 0.75 GB lift pad (no promote→allocate→shed loop).
    <EffectComposer multisampling={0}>
      <Bloom
        intensity={1.15}
        luminanceThreshold={0.55}
        luminanceSmoothing={0.2}
        radius={0.7}
        mipmapBlur
      />
      <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
    </EffectComposer>
  );
}
