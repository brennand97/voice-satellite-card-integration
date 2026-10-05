import path from 'node:path';
import { config, runtime } from '@homelab/ha-testbed';
export default config({ audio: path.join(runtime().directory, 'microphone.wav') });
