"""Satellite-owned fixtures/flows; no product patches or production deployment calls."""

import hashlib
import json
import os
import shutil
import socket
import subprocess
import wave
from pathlib import Path

from ha_testbed.api import wait_until

TIMER_TOOLS = ["voice_satellite__" + name for name in ["StartTimer", "StopTimer", "RenameTimer", "ExtendTimer", "ShortenTimer", "GetTimerStatus"]]


def repos(cfg):
    satellite = Path(cfg["root"]).parents[1]
    transport = Path(
        os.environ.get(
            "VOICE_TRANSPORT_REPO",
            satellite.parent / "pipecat-external-voice-transport",
        )
    ).resolve()
    if not (transport / "tests/e2e/Dockerfile").exists():
        raise RuntimeError(
            "Set VOICE_TRANSPORT_REPO to the transport repository with its E2E adapter"
        )
    return satellite, transport


def prepare(cfg, cache, docker):
    satellite, transport = repos(cfg)
    subprocess.run(["npm", "ci", "--no-audit", "--no-fund"], cwd=satellite, check=True)
    # Build-only; never npm run dev (which deploys to HA).
    subprocess.run(["npm", "run", "build"], cwd=satellite, check=True)
    subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund"], cwd=Path(cfg["root"]), check=True
    )
    docker(
        "build",
        "-f",
        transport / "tests/e2e/Dockerfile",
        "-t",
        "ha-testbed-transport:local",
        transport,
        capture=False,
    )
    image_id = json.loads(docker("inspect", "ha-testbed-transport:local").stdout)[0][
        "Id"
    ]
    return {
        "transport_image": image_id,
        "transport_repo": str(transport),
        "satellite_version": json.loads((satellite / "package.json").read_text())[
            "version"
        ],
    }


def stage(cfg, run, credentials, config):
    satellite, transport = repos(cfg)
    # Copy built assets, not bytecode; never mount into production.
    if not run.get("release"):
        shutil.copytree(
            satellite / "custom_components/voice_satellite",
            config / "custom_components/voice_satellite",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        if not (
            config
            / "custom_components/voice_satellite/frontend/voice-satellite-panel.js"
        ).exists():
            raise RuntimeError("Missing built frontend: run prepare first")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        transport_port = sock.getsockname()[1]
    run["transport_port"] = transport_port
    run["transport_url"] = f"http://localhost:{transport_port}"
    env = Path(run["directory"]) / "transport.env"
    env.write_text(
        "\n".join(
            [
                "EXTERNAL_TRANSPORT_TOKEN=" + credentials["transport_token"],
                "REALTIME_PROVIDER=fake",
                "PUBLIC_BASE_URL=" + run["transport_url"],
                "TRUSTED_TOOL_CONFIG_PATH=/fixtures/tools.json",
                "AUDIO_URL_SIGNING_KEY=" + credentials["signing_key"],
                "INPUT_IDLE_TIMEOUT_SECONDS=120",
                "MAX_SESSION_SECONDS=600",
            ]
        )
        + "\n"
    )
    env.chmod(0o600)
    tools = Path(run["directory"]) / "tools"
    tools.mkdir(mode=0o700)
    (tools / "tools.json").write_text(
        json.dumps(
            {
                "profiles": {"e2e": {"providers": [], "allowed_tools": []}},
                "default_profile": "e2e",
            }
        )
    )
    # Prerecorded speech + silence: input capture, not STT accuracy.
    pcm = (transport / "tests/fixtures/tell-me-a-short-joke.pcm").read_bytes()
    with wave.open(str(Path(run["directory"]) / "microphone.wav"), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00" * 32000 + pcm + b"\x00" * 64000)
    shutil.copytree(Path(cfg["root"]) / "fixtures/testbed_media", config / "custom_components/testbed_media", ignore=shutil.ignore_patterns("__pycache__"))
    with (config / "configuration.yaml").open("a") as out:
        out.write("""media_player:
  - platform: testbed_media
template:
  - light:
      - name: Testbed Light
        unique_id: testbed_light
        state: "{{ is_state('input_boolean.testbed_light', 'on') }}"
        turn_on:
          action: input_boolean.turn_on
          target:
            entity_id: input_boolean.testbed_light
        turn_off:
          action: input_boolean.turn_off
          target:
            entity_id: input_boolean.testbed_light
""")
    # Genuine Lovelace buttons initiate service-origin text/voice turns.
    with (config / "ui-lovelace.yaml").open("a") as out:
        out.write("""      - type: button
        name: Test text turn
        icon: mdi:message-text
        tap_action:
          action: perform-action
          perform_action: voice_satellite.show
          target:
            entity_id: assist_satellite.e2e_browser
          data:
            prompt: hello from browser
            silent: false
            duration: 10
      - type: button
        name: Wake test microphone
        icon: mdi:microphone
        tap_action:
          action: perform-action
          perform_action: voice_satellite.wake
          target:
            entity_id: assist_satellite.e2e_browser
      - type: button
        name: Test timer
        icon: mdi:timer
        tap_action:
          action: perform-action
          perform_action: voice_satellite.start_timer
          target:
            entity_id: assist_satellite.e2e_browser
          data:
            name: E2E Timer
            seconds: 8
""")
    files = sorted((config / "custom_components/voice_satellite").rglob("*"))
    digest = hashlib.sha256()
    for file in files:
        if file.is_file():
            digest.update(str(file.relative_to(config)).encode())
            digest.update(file.read_bytes())
    run["candidate_sha256"] = digest.hexdigest()


def start(cfg, run, credentials, docker):
    name = run["id"] + "-transport"
    if name not in run["containers"]:
        run["containers"].append(name)
    docker(
        "run",
        "-d",
        "--init",
        "--name",
        name,
        "--label",
        "io.ha-testbed.run=" + run["id"],
        "--network",
        run["id"],
        "--network-alias",
        "transport",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--stop-timeout",
        "10",
        "--env-file",
        Path(run["directory"]) / "transport.env",
        "-v",
        f"{Path(run['directory']) / 'tools'}:/fixtures:ro",
        run["prepared"]["adapter_data"]["transport_image"],
    )


def verify(cfg, run):
    import sys

    _, transport = repos(cfg)
    return subprocess.call(
        [
            sys.executable,
            str(transport / "tests/e2e/smoke.py"),
            str(Path(run["directory"]) / "run.json"),
        ]
    )


def configure_tools(cfg, run, credentials, ha):
    from ha_testbed.cli import docker

    ha.flow("mcp_server", [("user", {"llm_hass_api": ["assist"]})])
    ha.ws(
        {
            "type": "homeassistant/expose_new_entities/set",
            "assistant": "conversation",
            "expose_new": False,
        }
    )
    all_ids = [s["entity_id"] for s in ha.request("/api/states")]
    ha.ws(
        {
            "type": "homeassistant/expose_entity",
            "assistants": ["conversation"],
            "entity_ids": all_ids,
            "should_expose": False,
        }
    )
    ha.ws(
        {
            "type": "homeassistant/expose_entity",
            "assistants": ["conversation"],
            "entity_ids": ["light.testbed_light"],
            "should_expose": True,
        }
    )
    tools = {
        "mcp_servers": [
            {
                "name": "test-ha",
                "transport": "streamable_http",
                "url": "http://ha:8123/api/mcp",
                "bearer_token_env": "HOMEASSISTANT_MCP_TOKEN",
                "allowed_tools": ["intent__HassTurnOn", *TIMER_TOOLS],
            }
        ],
        "profiles": {
            "e2e": {
                "providers": ["test-ha"], "allowed_tools": ["intent__HassTurnOn", *TIMER_TOOLS],
                "context_injections": {name: {"device_id": "home_assistant_device_id"} for name in TIMER_TOOLS},
            }
        },
        "default_profile": "e2e",
    }
    (Path(run["directory"]) / "tools/tools.json").write_text(json.dumps(tools))
    with (Path(run["directory"]) / "transport.env").open("a") as out:
        out.write(
            "HOMEASSISTANT_MCP_TOKEN=" + credentials["tokens"]["access_token"] + "\n"
        )
    name = run["id"] + "-transport"
    docker("stop", name)
    docker("rm", name)
    start(cfg, run, credentials, docker)


def install_release(cfg, run, credentials, ha):
    from runpy import run_path

    return run_path(str(Path(cfg["root"]) / "release.py"))["install_release"](
        cfg, run, credentials, ha
    )


def configure(cfg, run, credentials, ha):
    wait_until(lambda: ha.request("/api/config").get("state") == "RUNNING")
    try:
        configure_tools(cfg, run, credentials, ha)
        run["tool_status"] = "ready"
    except Exception as exc:  # noqa: BLE001 - preserve partial debug fixtures
        run["tool_status"] = "failed"
        run["tool_error"] = str(exc)
    service = ha.flow(
        "voice_satellite",
        [
            ("user", {"entry_type": "external_conversation_service"}),
            (
                "external_service",
                {
                    "name": "E2E Transport",
                    "external_transport_url": "ws://transport:8080/transport/v1",
                    "external_transport_token": credentials["transport_token"],
                    "external_transport_verify_tls": True,
                    "external_transport_ready_timeout": 5,
                    "profile_name": "E2E Profile",
                    "tool_profile": "",
                },
            ),
        ],
    )
    satellite = ha.flow(
        "voice_satellite",
        [("user", {"entry_type": "satellite"}), ("satellite", {"name": "E2E Browser"})],
    )
    profiles = ha.ws(
        {"type": "config_entries/subentries/list", "entry_id": service["entry_id"]}
    )
    wait_until(
        lambda: [
            s
            for s in ha.request("/api/states")
            if s["entity_id"] == "assist_satellite.e2e_browser"
        ]
    )
    all_states = ha.request("/api/states")
    detection = next(
        s
        for s in all_states
        if s["entity_id"].startswith("select.e2e_browser")
        and "Disabled" in s["attributes"].get("options", [])
    )
    ha.request(
        "/api/services/select/select_option",
        {"entity_id": detection["entity_id"], "option": "Disabled"},
    )
    panel = {
        "satellite_entity": "assist_satellite.e2e_browser",
        "auto_start": True,
        "debug": True,
        "skin": "default",
        "reactive_bar": False,
        "_dsp_version": 4,
        "stt_noise_suppression": False,
        "stt_echo_cancellation": False,
        "stt_auto_gain_control": False,
    }
    ha.ws(
        {
            "type": "voice_satellite/save_panel_settings",
            "entity_id": "assist_satellite.e2e_browser",
            "config": panel,
        }
    )
    run["fixture"] = {
        "entity": "assist_satellite.e2e_browser",
        "service_entry": service["entry_id"],
        "profile_id": profiles[0]["subentry_id"],
        "satellite_entry": satellite["entry_id"],
        "panel": panel,
    }
    options = ha.request(
        "/api/config/config_entries/options/flow", {"handler": satellite["entry_id"]}
    )
    for step, payload in [
        ("init", {"conversation_service_entry_id": service["entry_id"]}),
        ("profile", {"conversation_profile_id": profiles[0]["subentry_id"]}),
        (
            "media_guard",
            {
                "media_guard_entities": ["media_player.testbed_speaker"],
                "media_guard_action": "duck",
                "media_guard_volume": 10,
                "media_guard_restore_delay_ms": 350,
            },
        ),
    ]:
        if options.get("step_id") != step:
            raise RuntimeError(
                f"Unexpected satellite options step: {options.get('step_id')}"
            )
        options = ha.request(
            "/api/config/config_entries/options/flow/" + options["flow_id"], payload
        )
    if options["type"] != "create_entry":
        raise RuntimeError(
            f"Satellite options flow rejected valid fixture: {options.get('errors')}"
        )
    transport_select = next(s for s in ha.request("/api/states") if s["entity_id"].startswith("select.e2e_browser") and "External" in s["attributes"].get("options", []))
    ha.request("/api/services/select/select_option", {"entity_id": transport_select["entity_id"], "option": "External"})
    return run["fixture"]
