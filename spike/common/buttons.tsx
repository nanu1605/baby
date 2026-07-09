// V0 shell spike — a couple of Motion-animated buttons, byte-identical across
// shells. These exercise the same DOM-motion stack (Motion, framer-motion
// successor) the V4 motion system will use, so the spike also sanity-checks that
// UI micro-motion composits cleanly over the WebGL canvas in each shell.

import { motion } from "framer-motion";

const spring = { type: "spring", stiffness: 400, damping: 22 } as const;

export function SpikeButtons() {
  return (
    <div style={{ display: "flex", gap: "0.6rem" }}>
      <motion.button
        style={btn("#6ea8fe")}
        whileHover={{ scale: 1.06, boxShadow: "0 0 18px rgba(110,168,254,0.6)" }}
        whileTap={{ scale: 0.94 }}
        transition={spring}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
      >
        Primary
      </motion.button>
      <motion.button
        style={btn("#2dd4bf")}
        whileHover={{ scale: 1.06, boxShadow: "0 0 18px rgba(45,212,191,0.6)" }}
        whileTap={{ scale: 0.94 }}
        transition={spring}
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
      >
        Secondary
      </motion.button>
    </div>
  );
}

function btn(accent: string): React.CSSProperties {
  return {
    padding: "0.5rem 1rem",
    borderRadius: 10,
    border: `1px solid ${accent}`,
    background: "rgba(20,24,33,0.75)",
    color: accent,
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
  };
}
