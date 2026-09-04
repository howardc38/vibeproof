// RED. A Google API key inlined into a React component, which also ships it to
// every browser that loads the bundle.
const MAPS_KEY = "AIza1qdXWPJx-d8ut8pdqsxRpvED5hJRaOvRTYZ";

export const mapsUrl = (q: string) =>
  `https://maps.googleapis.com/maps/api/geocode/json?address=${q}&key=${MAPS_KEY}`;
