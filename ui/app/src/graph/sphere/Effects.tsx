/**
 * Post-processing bloom (V3c) — the glow that makes the neural sphere read as
 * "alive". This is the #1 VRAM item, so it is rendered ONLY at the full3d tier
 * (the governor sheds it first on demote — see Scene's `plan.bloom` gate).
 */
import { Bloom, EffectComposer } from "@react-three/postprocessing";

export default function Effects() {
  return (
    <EffectComposer>
      <Bloom
        intensity={0.9}
        luminanceThreshold={0.2}
        luminanceSmoothing={0.9}
        mipmapBlur
      />
    </EffectComposer>
  );
}
