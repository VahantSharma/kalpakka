// Bundles the capture AudioWorklet (TypeScript + its tested imports) into one classic script
// under public/, which audioWorklet.addModule() can load. Runs before `dev` and `build`.
import { build } from "rolldown";

await build({
  input: "src/lib/audio/capture.worklet.ts",
  output: { file: "public/capture-worklet.js", format: "esm", minify: false },
  logLevel: "warn",
});
console.log("built public/capture-worklet.js");
