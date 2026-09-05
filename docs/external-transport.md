# External Transport

External Transport is a provider-neutral conversation path for a Voice Satellite. It keeps Kiosk Satellite's native wake-word and binary PCM/pre-roll path while relaying a turn to an authenticated external voice service.

## Configuration

1. Open the Voice Satellite integration's **Configure** flow.
2. Set the External Transport WebSocket URL and token.
3. On the satellite, set **Conversation transport** to **External**.

Credentials are stored in the Home Assistant config-entry options and are not exposed as entity attributes.

## Safety

The original **Home Assistant** transport remains the default and fallback option. An External turn is not automatically replayed through Assist after it has sent audio, because doing so could duplicate a home-control action.

## Protocol

The external endpoint implements Protocol v1 documented in `external-transport-protocol.md`. It receives raw 16 kHz mono PCM16LE as binary WebSocket frames after responding with `session.ready`.
