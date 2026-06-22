"""HTTP webhook request utilities."""

import json
import logging
import time

import requests

from lockbot.core.config import Config

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2
_RETRY_DELAY = 1.0
_DEFAULT_HEADERS = {"Content-Type": "application/json"}


def _first_cell_empty(line: str) -> bool:
    """Return True if the markdown row's first column cell is empty (whitespace only)."""
    parts = line.split("|")
    return len(parts) > 1 and parts[1].strip() == ""


def _adjust_split_for_node_boundary(content: str, split_index: int) -> int:
    """Adjust split_index backward to avoid splitting a node's rows across chunks.

    If the chunk that would follow split_index starts with a continuation row
    (empty first cell, i.e. a device row whose node name is on a previous line),
    walk backward in content[:split_index] to find the newline just before that
    node's first row, and return that position instead.
    """
    remainder = content[split_index:].lstrip()
    first_line = remainder.split("\n", 1)[0] if remainder else ""
    if not (first_line.startswith("|") and _first_cell_empty(first_line)):
        return split_index

    # Walk backwards to find the last row with a non-empty, non-separator first cell
    before = content[:split_index]
    pos = len(before)
    while pos > 0:
        nl = before.rfind("\n", 0, pos)
        if nl == -1:
            return split_index
        line_start = nl + 1
        line_end = before.find("\n", line_start)
        line = before[line_start:] if line_end == -1 else before[line_start:line_end]
        if line.startswith("|") and not _first_cell_empty(line):
            sep = line.replace(" ", "").replace("-", "").replace("|", "")
            if sep != "":  # not a separator row (| --- | --- |)
                return nl if nl > 0 else split_index
        pos = nl
    return split_index


def _extract_md_table_header(content: str) -> str:
    """Extract the markdown table header (column + separator rows) from content.

    Returns the header string (including trailing newline) if found, else "".
    A markdown table header is two consecutive lines matching:
        | col | col | ...
        | --- | --- | ...
    """
    lines = content.split("\n")
    for i in range(len(lines) - 1):
        if lines[i].startswith("|") and lines[i + 1].startswith("|"):
            sep = lines[i + 1].replace(" ", "").replace("-", "").replace("|", "")
            if sep == "":
                return lines[i] + "\n" + lines[i + 1] + "\n"
    return ""


def post_webhook(msg, config=None):
    """Send a message via webhook, splitting long TEXT/MD content into chunks.

    For MD messages that contain a markdown table, subsequent chunks are
    prepended with the table header so the platform can render them correctly.

    Args:
        msg: Message dict with structure {"message": {"header": {}, "body": []}}.
        config: Optional Config instance; uses global Config if None.

    Returns:
        list of (status_code, response_text) tuples.
    """
    MAX_LENGTH = 2000
    if config is not None:
        webhook_url = config.get_val("WEBHOOK_URL")
    else:
        webhook_url = Config.get("WEBHOOK_URL")

    # Extract the first TEXT/MD body; everything after it goes to the last chunk only
    text_body = None
    trailing_bodies = []
    for body in msg["message"]["body"]:
        if body.get("type") in ("TEXT", "MD") and text_body is None:
            text_body = body
        else:
            trailing_bodies.append(body)

    body_type = text_body["type"] if text_body else "TEXT"

    new_msgs = []
    if text_body:
        content = text_body["content"]
        # For MD messages, detect table header to prepend on continuation chunks
        md_table_header = _extract_md_table_header(content) if body_type == "MD" else ""
        # Split long content: prefer newline near end, otherwise hard-split at MAX_LENGTH
        while len(content) > MAX_LENGTH:
            split_index = content.rfind("\n", 0, MAX_LENGTH)
            if split_index == -1 or split_index < int(MAX_LENGTH * 0.8):
                split_index = MAX_LENGTH
            # For MD table content, try to avoid splitting a node's device rows across chunks
            if md_table_header:
                split_index = _adjust_split_for_node_boundary(content, split_index)
                if split_index <= 0:
                    split_index = MAX_LENGTH
            part = content[:split_index]
            new_msgs.append(
                {
                    "message": {
                        "header": msg["message"]["header"],
                        "body": [{"type": body_type, "content": part}],
                    }
                }
            )
            remainder = content[split_index:].lstrip()
            # Prepend table header on continuation chunks so MD renders correctly
            content = (md_table_header + remainder) if md_table_header else remainder
        new_msgs.append(
            {
                "message": {
                    "header": msg["message"]["header"],
                    "body": [{"type": body_type, "content": content}] + trailing_bodies,
                }
            }
        )
    else:
        new_msgs.append(msg)

    responses = []
    for i, new_msg in enumerate(new_msgs):
        logger.debug("Webhook payload [%d/%d]: %s", i + 1, len(new_msgs), json.dumps(new_msg, ensure_ascii=False))
        resp = _post_with_retry(webhook_url, new_msg, _DEFAULT_HEADERS)
        responses.append(resp)
    return responses


def _post_with_retry(url, payload, headers):
    """POST with simple retry on failure. Returns (status_code, response_text)."""
    last_exc = None
    for attempt in range(_MAX_RETRIES + 1):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=5)
            if 200 <= response.status_code < 300:
                return (response.status_code, response.text)
            logger.warning(
                "Webhook POST to %s returned %d (attempt %d/%d): %s",
                url,
                response.status_code,
                attempt + 1,
                _MAX_RETRIES + 1,
                response.text[:200],
            )
        except requests.exceptions.RequestException as e:
            last_exc = e
            logger.warning(
                "Webhook POST to %s failed (attempt %d/%d): %s",
                url,
                attempt + 1,
                _MAX_RETRIES + 1,
                e,
            )
        if attempt < _MAX_RETRIES:
            time.sleep(_RETRY_DELAY)

    if last_exc:
        logger.error("Webhook POST to %s failed after %d attempts: %s", url, _MAX_RETRIES + 1, last_exc)
        return (None, str(last_exc))
    # All attempts returned non-2xx — return the last response
    return (response.status_code, response.text)
