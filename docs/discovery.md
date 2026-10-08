# Installed definitions

The loader reads Grid modules and modulators from the installed Bitwig `Library/modules` and `Library/modulators` directories. It uses the app's `device-settings/<UUID>/Default.bwpreset` files as Grid skeletons. A fresh installation therefore does not need Jeremy's library or manually saved module examples.

Bitwig 6.1.3 provides 233 module definitions and 43 modulator definitions, including HW CV In. An optional preset corpus can contribute legacy types, descriptors and aliases. Definition-derived names and parameter representations take precedence over customized preset names. Exact canonical names take precedence over aliases.

Recognized authoring data includes scalar/enum values, input/output ports, modulation sources, text, step arrays, flags, curves, Sampler/sample-player payloads, wavetables and source selectors. Internal DSP/UI atoms and interactive Learn/Clear/Nudge buttons are not persistent authoring parameters; author their resulting settings or data directly.

## Paths

- `BITWIG_HOME` selects an installation directory or macOS `.app` bundle. Default probes cover macOS, `/opt/bitwig-studio` and the standard Windows Program Files directory.
- `BITWIG_LIBRARY_ROOT` overrides the `Library` resource directory when the installation layout differs.
- `BWGRID_CACHE_HOME` selects the schema cache directory.
- `BWGRID_CORPUS_PATHS` supplies additional OS-path-separated corpus roots. Set it to an empty string for app-resources-only discovery.

The loader finds `bitwig.jar` under the selected installation. Format-4 decoding reads the encoding definition from that JAR without requiring a JDK. The cache fingerprint includes the installation location and every consumed definition/preset file's size and nanosecond modification time. `bwgrid refresh` forces rebuilding it.

Bitwig remains a runtime requirement for authoring from its definitions. The public package includes neither vendor definitions/audio nor private preset dumps. Synthetic tests can run without an installation; real-corpus checks are conditional. macOS 6.1.3 is the inspected installation. Other platforms have configurable paths, but have not been live-verified.
