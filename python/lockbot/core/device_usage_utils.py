"""Device usage display formatting and heterogeneous GPU detection."""

from lockbot.core.i18n import t
from lockbot.core.usage_render import min_remaining, render_line, sort_and_group
from lockbot.core.utils import (
    format_access_mode,
    format_duration,
    remaining_duration,
)

_DEFAULT_LINE_TEMPLATE = "{node} {dev} {model}{user}{mode} {dur}"
_DEFAULT_IDLE_TEMPLATE = "{node} {dev} {model}{status}"


def format_usage_line(dev_range, model_str, user_or_status, duration, has_merged=False):
    """
    Format a single device info line.
    """
    dev_range_width = 5 if not has_merged else 7
    if model_str:
        return f"{dev_range:<{dev_range_width}} {model_str} {user_or_status} {duration}"
    else:
        return f"{dev_range:<{dev_range_width}} {user_or_status} {duration}"


def group_locked_devices(node_status):
    """
    Group locked devices by user/model/shared-mode, returning a list of
    (key, device_indices) tuples.
    """
    current_group = []
    grouped_usage = []
    prev_user = None

    for idx, dev in enumerate(node_status):
        users = dev["current_users"]
        if dev["status"] != "idle":
            status = dev["status"]
            model = dev["dev_model"]

            user_keys = [(user["user_id"], user["start_time"], user["duration"]) for user in users]
            user_keys_sorted = sorted(user_keys, key=lambda x: x[0])

            key = (model, status, user_keys_sorted)

            if prev_user is None or key == prev_user:
                current_group.append(idx)
            else:
                grouped_usage.append((prev_user, current_group))
                current_group = [idx]
            prev_user = key
        else:
            if current_group:
                grouped_usage.append((prev_user, current_group))
                current_group = []
            prev_user = None

    if current_group:
        grouped_usage.append((prev_user, current_group))
    return grouped_usage


def group_idle_devices(node_status, exclude_indices):
    """
    Group consecutive idle devices by model, using dev_id for continuity check.
    """
    groups = []
    current = []
    prev_model = None
    prev_devid = None

    for idx, dev in enumerate(node_status):
        if idx in exclude_indices:
            if current:
                groups.append((current, prev_model))
                current = []
            prev_model = None
            prev_devid = None
            continue

        if dev["status"] == "idle":
            model = dev["dev_model"]
            devid = dev["dev_id"]
            if prev_model == model and prev_devid is not None and dev["dev_id"] == prev_devid + 1:
                current.append(idx)
            else:
                if current:
                    groups.append((current, prev_model))
                current = [idx]
                prev_model = model
            prev_devid = devid
        else:
            if current:
                groups.append((current, prev_model))
                current = []
            prev_model = None
            prev_devid = None

    if current:
        groups.append((current, prev_model))

    return groups


def _is_heterogeneous(node_status):
    """Check if a node has heterogeneous device models."""
    models = set()
    for dev in node_status or []:
        m = dev.get("dev_model", "")
        if m:
            models.add(m)
        if len(models) > 1:
            return True
    return False


def render_device_lines(node_status, grouped_usage, idle_groups, config=None):
    """
    Generate (is_idle, fields) rows from locked and idle device groups.

    Each row is a tuple (is_idle: bool, fields: dict). fields keys:
    node (always "" — filled by caller), dev, model, user, mode, dur, status.
    """
    rows = []
    show_model = _is_heterogeneous(node_status)
    all_segments = []

    for key, dev_ids in grouped_usage:
        model, status, user_keys_sorted = key
        all_segments.append((dev_ids[0], "lock", (user_keys_sorted, status, dev_ids, model)))

    for group, model in idle_groups:
        all_segments.append((group[0], "idle", (group, model)))

    for _, tag, data in sorted(all_segments, key=lambda x: x[0]):
        if tag == "lock":
            user_keys_sorted, status, dev_ids, model = data
            for user_idx, (user_id, start_time, duration) in enumerate(user_keys_sorted):
                if len(dev_ids) > 1:
                    dev_range = f"dev{dev_ids[0]}-{dev_ids[-1]}"
                else:
                    dev_range = f"dev{dev_ids[0]}"
                dev_range = dev_range if user_idx == 0 else ""
                model_str = f"{model} " if show_model and user_idx == 0 else ""
                duration_str = format_duration(remaining_duration(start_time, duration), config=config)
                rows.append(
                    (
                        False,
                        {
                            "node": "",
                            "dev": dev_range,
                            "model": model_str,
                            "user": user_id,
                            "mode": format_access_mode(status, config=config),
                            "dur": duration_str,
                            "status": "",
                        },
                    )
                )
        elif tag == "idle":
            group, model = data
            if len(group) > 1:
                dev_range = f"dev{group[0]}-{group[-1]}"
            else:
                dev_range = f"dev{group[0]}"
            model_str = f"{model} " if show_model else ""
            rows.append(
                (
                    True,
                    {
                        "node": "",
                        "dev": dev_range,
                        "model": model_str,
                        "user": "",
                        "mode": "",
                        "dur": "",
                        "status": t("status.idle", config=config),
                    },
                )
            )
    return rows


def get_current_usage(node_filter, bot_state, monitor_status, config=None):
    """
    Render device usage. Layout controlled by USAGE_SORT / USAGE_GROUP /
    USAGE_LINE_TEMPLATE / USAGE_IDLE_TEMPLATE on the bot config.
    """
    line_tpl = config.get_val("USAGE_LINE_TEMPLATE") if config else _DEFAULT_LINE_TEMPLATE
    idle_tpl = config.get_val("USAGE_IDLE_TEMPLATE") if config else _DEFAULT_IDLE_TEMPLATE
    sort_mode = config.get_val("USAGE_SORT") if config else "dur_asc"
    group_mode = config.get_val("USAGE_GROUP") if config else "idle_first"
    bot_name = config.get_val("BOT_NAME") if config else None
    fb_line = _DEFAULT_LINE_TEMPLATE
    fb_idle = _DEFAULT_IDLE_TEMPLATE

    # Build one entry per node, each carrying its rendered field-dict rows.
    entries = []
    order = 0
    for node_key, node_status in bot_state.items():
        if not (
            node_filter is None
            or node_key == node_filter
            or (isinstance(node_filter, list) and node_key in node_filter)
        ):
            continue
        grouped_usage = group_locked_devices(node_status)
        shown_indices = set()
        for _, dev_ids in grouped_usage:
            shown_indices.update(dev_ids)
        idle_groups = group_idle_devices(node_status, shown_indices)
        device_rows = render_device_lines(node_status, grouped_usage, idle_groups, config=config)
        rem = min_remaining(node_status)
        entries.append(
            {
                "order_index": order,
                "is_idle": rem is None,
                "min_remaining": rem,
                "node_key": node_key,
                "rows": device_rows,
            }
        )
        order += 1

    ordered = sort_and_group(entries, sort_mode, group_mode)

    usage_info = ""
    for entry in ordered:
        node_key = entry["node_key"]
        first = True
        for is_idle, fields in entry["rows"]:
            fields = dict(fields)
            fields["node"] = node_key if first else " " * len(node_key)
            tpl, fb = (idle_tpl, fb_idle) if is_idle else (line_tpl, fb_line)
            usage_info += render_line(tpl, fields, fb, bot_name=bot_name).rstrip() + "\n"
            first = False

    if node_filter:
        keys = node_filter if isinstance(node_filter, list) else [node_filter]
        if any(_is_heterogeneous(bot_state.get(k, [])) for k in keys):
            usage_info += t("device_usage.hetero_warning", config=config, node_key=node_filter)

    return usage_info
