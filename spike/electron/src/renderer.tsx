// V0 shell spike — Electron renderer entry. Mounts the byte-identical shared app.
import { createRoot } from "react-dom/client";
import { SpikeApp } from "@baby/spike-common/App";

createRoot(document.getElementById("root")!).render(<SpikeApp shell="electron" />);
