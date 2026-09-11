/**
 * Kiosk Satellite Skin
 *
 * Google Home layout (left-aligned text, Material cards, frosted overlay)
 * in the Kiosk Satellite app's palette: warm neutral surfaces, teal
 * accents, and the four logo bars as the activity indicator.
 */

import css from './kiosk-satellite.css';
import previewCSS from './kiosk-satellite-preview.css';

export const kioskSatelliteSkin = {
  id: 'kiosk-satellite',
  name: 'Kiosk Satellite',
  css,
  reactiveBar: true,
  hasDarkTheme: true,
  overlayColor: [245, 244, 242],
  darkOverlayColor: [32, 33, 36],
  defaultOpacity: 1,
  darkDefaultOpacity: 1,
  previewCSS,
};
