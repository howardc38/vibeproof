// RED. A Google API key inlined into a React component, which also ships it to
// Synthetic: generated for this fixture, never issued, never valid.
// Recorded because location is never evidence -- only the value is -- so a
// look-alike with no provenance leaves nobody able to say whether it needs
// rotating.
// every browser that loads the bundle.
const MAPS_KEY = "AIza1qdXWPJx-d8ut8pdqsxRpvED5hJRaOvRTYZ";

export const mapsUrl = (q: string) =>
  `https://maps.googleapis.com/maps/api/geocode/json?address=${q}&key=${MAPS_KEY}`;