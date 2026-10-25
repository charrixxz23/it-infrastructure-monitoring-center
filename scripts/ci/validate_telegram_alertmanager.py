"""Validate the opt-in Telegram route without credentials or network access."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/alertmanager/alertmanager.yml"
TELEGRAM_CONFIG = ROOT / "config/alertmanager/alertmanager.telegram.yml"
RULES_FILE = ROOT / "config/prometheus/rules/home-network.yml"
TEMPLATE_FILE = ROOT / "config/alertmanager/templates/telegram-home-network.tmpl"
COMPOSE_OVERRIDE = ROOT / "compose.telegram.yaml"
COMPOSE_DEFAULT = ROOT / "compose.yaml"
SECRET_FILES = {
    ".secrets/telegram-bot-token",
    ".secrets/telegram-chat-id",
}


def fail(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


def run_amtool(amtool: str, *args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [amtool, *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as error:
        # Do not forward command output: validation diagnostics should never echo
        # file contents or credentials if a future configuration is mis-specified.
        raise RuntimeError("amtool validation failed or timed out") from error


def read_yaml(path: Path) -> dict:
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected a YAML mapping in {path.relative_to(ROOT)}")
    return value


def main() -> int:
    amtool = shutil.which("amtool")
    if amtool is None:
        return fail(
            "amtool was not found; install the Alertmanager 0.34.1 validation binary."
        )

    try:
        version = run_amtool(amtool, "--version").stdout
        if "0.34.1" not in version:
            return fail("the validation binary must match Alertmanager 0.34.1")

        default_config = read_yaml(DEFAULT_CONFIG)
        opted_config = read_yaml(TELEGRAM_CONFIG)
        rules = read_yaml(RULES_FILE)
        compose_override = read_yaml(COMPOSE_OVERRIDE)
        compose_default = read_yaml(COMPOSE_DEFAULT)

        home_rules = {
            rule["alert"]
            for group in rules.get("groups", [])
            for rule in group.get("rules", [])
            if rule.get("labels", {}).get("source") == "home-network"
        }
        expected_rules = {
            "BlackboxExporterDown",
            "BlackboxExporterTargetMissing",
            "HomeProbeExecutionError",
            "HomeRouterTargetMissing",
            "HomeRouterUnavailable",
            "HomeDNSResolutionUnavailable",
            "HomeInternetHTTPSUnavailable",
        }
        if home_rules != expected_rules:
            return fail(
                "home-network alert rules changed; review the Telegram allowlist explicitly"
            )

        default_receivers = default_config.get("receivers", [])
        if "telegram" in json.dumps(default_config).lower():
            return fail(
                "the stable default configuration must not enable a Telegram receiver"
            )
        default_route = default_config.get("route", {})
        if (
            default_route.get("receiver") != "incident-logger"
            or default_route.get("group_by")
            != ["alertname", "instance", "mountpoint", "source"]
            or default_route.get("group_wait") != "3s"
            or default_route.get("group_interval") != "5s"
            or default_route.get("repeat_interval") != "4h"
            or default_route.get("routes")
        ):
            return fail("default Alertmanager routing or grouping changed unexpectedly")
        logger = next(
            (
                receiver
                for receiver in default_receivers
                if receiver.get("name") == "incident-logger"
            ),
            None,
        )
        if (
            logger is None
            or logger.get("webhook_configs", [{}])[0].get("send_resolved") is not True
        ):
            return fail(
                "the default Incident Logger receiver or resolved delivery changed"
            )
        default_alertmanager = compose_default.get("services", {}).get(
            "alertmanager", {}
        )
        if (
            "--config.file=/etc/alertmanager/alertmanager.yml"
            not in default_alertmanager.get("command", [])
        ):
            return fail(
                "default Compose must continue to load the stable Alertmanager config"
            )
        if any(
            ".secrets" in mount for mount in default_alertmanager.get("volumes", [])
        ):
            return fail(
                "default Compose must not mount the optional Telegram secret files"
            )
        if opted_config.get("receivers", []).count(logger) != 1:
            return fail(
                "the opt-in configuration must preserve the default Incident Logger receiver"
            )

        telegram_receivers = [
            receiver
            for receiver in opted_config.get("receivers", [])
            if receiver.get("name") == "telegram-home-network"
        ]
        if len(telegram_receivers) != 1:
            return fail(
                "the opt-in configuration must define exactly one Telegram receiver"
            )
        telegram_configs = telegram_receivers[0].get("telegram_configs", [])
        if len(telegram_configs) != 1:
            return fail(
                "the opt-in Telegram receiver must contain exactly one configuration"
            )
        telegram = telegram_configs[0]
        if (
            telegram.get("bot_token_file") != "/run/secrets/telegram_bot_token"
            or telegram.get("chat_id_file") != "/run/secrets/telegram_chat_id"
            or telegram.get("send_resolved") is not True
            or telegram.get("parse_mode") != ""
            or "bot_token" in telegram
            or "chat_id" in telegram
        ):
            return fail(
                "Telegram must use separate secret files, plain text, and resolved delivery"
            )

        routes = opted_config.get("route", {}).get("routes", [])
        opted_root = opted_config.get("route", {})
        if (
            opted_root.get("receiver") != "incident-logger"
            or opted_root.get("group_by") != default_route.get("group_by")
            or opted_root.get("group_wait") != default_route.get("group_wait")
            or opted_root.get("group_interval") != default_route.get("group_interval")
            or opted_root.get("repeat_interval") != default_route.get("repeat_interval")
        ):
            return fail(
                "opt-in config must preserve the default root route and grouping"
            )
        if len(routes) != 2 or routes[0].get("receiver") != "telegram-home-network":
            return fail(
                "expected a Telegram route followed by the preserved Incident Logger route"
            )
        if (
            routes[0].get("continue") is not True
            or routes[1].get("receiver") != "incident-logger"
        ):
            return fail(
                "Telegram routing must continue to the Incident Logger sibling route"
            )
        expected_matchers = {
            'source="home-network"',
            'alertname=~"^(' + "|".join(sorted(expected_rules)) + ')$"',
        }
        for route in routes:
            if set(route.get("matchers", [])) != expected_matchers:
                return fail(
                    "Telegram and paired logger routes must use the exact home-network allowlist"
                )
        if (
            routes[0].get("group_wait") != "30s"
            or routes[0].get("group_interval") != "5m"
        ):
            return fail("Telegram grouping wait/interval must remain 30s / 5m")

        template = TEMPLATE_FILE.read_text(encoding="utf-8")
        if re.search(
            r"\.Labels\.(?:instance|device|mountpoint|ssid|mac|ip)\b",
            template,
            re.IGNORECASE,
        ):
            return fail(
                "the Telegram message template must not render host or network identifiers"
            )
        if (
            "alertmanager.telegram.yml"
            not in compose_override.get("services", {})
            .get("alertmanager", {})
            .get("command", [""])[0]
        ):
            return fail(
                "the optional Compose override must select only the alternate Alertmanager config"
            )
        if opted_config.get("templates") != [
            "/etc/alertmanager/templates/telegram-home-network.tmpl"
        ]:
            return fail("opt-in Alertmanager must load its mounted message template")
        mounts = compose_override["services"]["alertmanager"].get("volumes", [])
        secret_mounts = {
            mount["source"].removeprefix("./"): mount
            for mount in mounts
            if mount.get("source", "").removeprefix("./") in SECRET_FILES
        }
        if set(secret_mounts) != SECRET_FILES or any(
            not mount.get("read_only")
            or mount.get("bind", {}).get("create_host_path") is not False
            for mount in secret_mounts.values()
        ):
            return fail(
                "both separate secret file mounts must be read-only and must not auto-create"
            )
        required_read_only_targets = {
            "/etc/alertmanager/alertmanager.telegram.yml",
            "/etc/alertmanager/templates/telegram-home-network.tmpl",
        }
        config_mounts = {
            mount.get("target"): mount for mount in mounts if mount.get("target")
        }
        if any(
            target not in config_mounts or not config_mounts[target].get("read_only")
            for target in required_read_only_targets
        ):
            return fail("opt-in config and template files must be mounted read-only")
        ignore_text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        if ".secrets/" not in ignore_text:
            return fail("the local secrets directory must remain ignored by Git")

        config_path = str(TELEGRAM_CONFIG)
        run_amtool(amtool, "check-config", str(DEFAULT_CONFIG))
        run_amtool(amtool, "check-config", config_path)

        expected_receivers = "telegram-home-network,incident-logger"
        for alert_name in sorted(expected_rules):
            run_amtool(
                amtool,
                "config",
                "routes",
                "test",
                f"--config.file={config_path}",
                f"--verify.receivers={expected_receivers}",
                f"alertname={alert_name}",
                "source=home-network",
                "severity=warning",
            )
        run_amtool(
            amtool,
            "config",
            "routes",
            "test",
            f"--config.file={config_path}",
            "--verify.receivers=incident-logger",
            "alertname=UnapprovedTestAlert",
            "source=home-network",
            "severity=warning",
        )
        run_amtool(
            amtool,
            "config",
            "routes",
            "test",
            f"--config.file={config_path}",
            "--verify.receivers=incident-logger",
            "alertname=HighCPUUsage",
            "source=infrastructure",
            "severity=critical",
        )

        with tempfile.TemporaryDirectory(
            prefix="itmc-telegram-template-"
        ) as temporary_dir:
            data_path = Path(temporary_dir) / "notification.json"
            message_text = '{{ template "telegram.home_network.message" . }}'
            synthetic_summary = "Synthetic <tag> & **markup** template check"
            for status, heading in (("firing", "FIRING"), ("resolved", "RESOLVED")):
                data = {
                    "receiver": "telegram-home-network",
                    "status": status,
                    "alerts": [
                        {
                            "status": status,
                            "labels": {
                                "alertname": "HomeRouterUnavailable",
                                "severity": "critical",
                                "source": "home-network",
                                "instance": "synthetic-target-not-for-delivery",
                            },
                            "annotations": {"summary": synthetic_summary},
                        }
                    ],
                    "groupLabels": {"alertname": "HomeRouterUnavailable"},
                    "commonLabels": {
                        "alertname": "HomeRouterUnavailable",
                        "severity": "critical",
                        "source": "home-network",
                    },
                    "commonAnnotations": {"summary": synthetic_summary},
                    "externalURL": "http://alertmanager.example.test",
                }
                data_path.write_text(json.dumps(data), encoding="utf-8")
                rendered = run_amtool(
                    amtool,
                    "template",
                    "render",
                    f"--template.glob={TEMPLATE_FILE}",
                    f"--template.data={data_path}",
                    f"--template.text={message_text}",
                ).stdout
                if (
                    heading not in rendered
                    or "HomeRouterUnavailable" not in rendered
                    or synthetic_summary not in rendered
                    or "synthetic-target-not-for-delivery" in rendered
                ):
                    return fail(
                        f"the {status} template output failed safe rendering assertions"
                    )

    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        yaml.YAMLError,
        RuntimeError,
    ) as error:
        return fail(str(error))

    print(
        "PASS: default config remains Telegram-free; opt-in config, allowlisted routing, "
        "synthetic FIRING/RESOLVED templates, and privacy assertions validated without network delivery."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
