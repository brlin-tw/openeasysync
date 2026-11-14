# Copyright 2026 林博仁(Buo-ren Lin) <buo.ren.lin@gmail.com>
# SPDX-License-Identifier: AGPL-3.0-or-later
"""<OTIMSG> XML handshake parser and generator."""

import platform
import re


def build_oti_msg(
    msg_type: str,
    command: str,
    os_version: str | None = None,
    computer_name: str | None = None,
    parameter: str | None = None,
    result: str | None = None,
) -> str:
    """Builds a compliant <OTIMSG> XML string adhering to OTi-2108 specifications."""
    if os_version is None:
        os_version = f"{platform.system()} {platform.release()}"
    if computer_name is None:
        computer_name = platform.node() or "localhost"

    lines = [
        "<OTIMSG>",
        f"    <MSG_TYPE>{msg_type}</MSG_TYPE>",
        f"    <COMMAND>{command}</COMMAND>",
    ]
    if parameter is not None:
        lines.append(f"    <PARAMETER>{parameter}</PARAMETER>")
    if result is not None:
        lines.append(f"    <RESULT>{result}</RESULT>")

    lines.extend(
        [
            "    < SYSTEM_INFO >",
            f"        <OS_VERSION>{os_version}</OS_VERSION>",
            f"        <COMPUTER_NAME>{computer_name}</COMPUTER_NAME>",
            "    </ SYSTEM_INFO >",
            "</OTIMSG>",
        ]
    )
    return "\r\n".join(lines) + "\r\n"


def parse_oti_msg(xml_str: str) -> dict[str, str]:
    """Parses an <OTIMSG> XML document, extracting key fields."""
    result: dict[str, str] = {}

    def extract_tag(tag_name: str) -> str | None:
        # Flexible match for tags that might have whitespace like < SYSTEM_INFO >
        pattern = rf"<{re.escape(tag_name)}\s*>(.*?)</{re.escape(tag_name)}\s*>"
        match = re.search(pattern, xml_str, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else None

    msg_type = extract_tag("MSG_TYPE")
    if msg_type:
        result["msg_type"] = msg_type

    command = extract_tag("COMMAND")
    if command:
        result["command"] = command

    param = extract_tag("PARAMETER")
    if param:
        result["parameter"] = param

    res = extract_tag("RESULT")
    if res:
        result["result"] = res

    os_ver = extract_tag("OS_VERSION")
    if os_ver:
        result["os_version"] = os_ver

    comp_name = extract_tag("COMPUTER_NAME")
    if comp_name:
        result["computer_name"] = comp_name

    return result
