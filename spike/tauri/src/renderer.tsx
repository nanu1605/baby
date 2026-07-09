// V0 shell spike — Tauri renderer entry. Installs the Tauri window.spikeAPI glue
// (side-effect import) then mounts the byte-identical shared app.
import "./spikeApiTauri";
import { createRoot } from "react-dom/client";
import { SpikeApp } from "@baby/spike-common/App";

createRoot(document.getElementById("root")!).render(<SpikeApp shell="tauri" />);
