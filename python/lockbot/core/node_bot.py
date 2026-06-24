"""Node lock bot with whole-node exclusive and shared locking support."""

import re
import time

from lockbot.core.base_bot import (
    BaseLockBot,
    BotState,  # noqa: F401  # re-export
)
from lockbot.core.i18n import t
from lockbot.core.io import (
    create_or_load_node_state,
    log_to_file,
    save_bot_state_to_file,
)
from lockbot.core.query_render import _get_ip, build_node_query
from lockbot.core.usage_render import (
    DEFAULT_IDLE_TEMPLATE,
    DEFAULT_LINE_TEMPLATE,
    min_remaining,
    render_line,
    sort_and_group,
)
from lockbot.core.utils import (
    create_user_info,
    duration_to_seconds,
    find_user_info,
    format_access_mode,
    format_duration,
    remaining_duration,
    remove_user_info,
)
from lockbot.core.xpu_collector import collect_node_usage

# Regex building blocks for command parsing
_NODE_LIST = r"([\w\d]+)(\s*[,，]\s*[\w\d]+)*"  # node1,node2,...
_DURATION = r"([0-9]+\.?[0-9]*)([dhm])"  # e.g. 3d, 2.5h, 30m


class NodeBot(BaseLockBot):
    """
    NodeBot class.
    """

    class _state_class(BotState):
        _loader = staticmethod(create_or_load_node_state)

    # Whether /query collects per-node GPU memory via SSH to drive the
    # memory-based status badge + 7-column table. QueueBot opts out.
    _collect_xpu_on_query = True

    def supported_commands(self):
        return ["lock", "slock", "unlock", "free", "kickout", "help", "h", "query"]

    def parse_command(self, user_id, command_key, command, parsing_duration=False):
        """
        Parse command
        """
        parse_ok = False
        error_reply = ""
        node_keys = []
        duration = 0

        def _get_return_values():
            return parse_ok, error_reply, node_keys, duration

        if parsing_duration:
            command_pattern = rf"^\s*({command_key})\s+({_NODE_LIST})\s*(\s{_DURATION})?\s*$"
        else:
            command_pattern = rf"^\s*({command_key})\s+({_NODE_LIST})\s*$"

        m = re.match(command_pattern, command)
        if not m:
            error_reply = self.print_help(
                user_id, t("error.invalid_command_format", config=self.config, command=command)
            )
            return _get_return_values()

        cluster_configs = self.config.get_val("CLUSTER_CONFIGS")
        node_keys = [id.strip() for id in re.split(r"[,，]", m[2])]
        for _, node_key in enumerate(node_keys):
            if node_key not in cluster_configs:
                error_reply = self.show_error(
                    user_id,
                    t(
                        "error.invalid_node_key",
                        config=self.config,
                        node_key=node_key,
                        valid_keys=str(list(cluster_configs.keys())),
                    ),
                )
                return _get_return_values()

        node_keys = list(set(node_keys))

        if parsing_duration:
            if m[7]:
                duration_unit = m[7]
                duration = int(duration_to_seconds(float(m[6]), duration_unit))
            else:
                duration = self.config.get_val("DEFAULT_DURATION")

            if duration == 0:
                error_reply = self.show_error(user_id, t("error.duration_must_be_positive", config=self.config))
                return _get_return_values()

        parse_ok = True
        return _get_return_values()

    def query(self, user_id, node_key=None):
        """
        Query usage of a node
        """
        # Collect GPU memory (blocking SSH on cache miss) OUTSIDE the lock so it
        # does not stall user commands or the scheduler's _check_and_notify, which
        # contend on the same self._lock. node_ips is read under the lock since it
        # touches bot_state; the SSH I/O itself runs lock-free. QueueBot disables
        # this via _collect_xpu_on_query.
        xpu_usage = None
        if self._collect_xpu_on_query:
            with self._lock:
                node_ips = self._node_ips(node_filter=node_key)
            xpu_usage = collect_node_usage(node_ips, self.config) if node_ips else None
        with self._lock:
            content = build_node_query(
                self.state.bot_state,
                user_id,
                self.config,
                node_filter=node_key,
                xpu_usage=xpu_usage,
                memory_based=self._collect_xpu_on_query,
            )
            return self.adapter.build_reply(content, [user_id], markdown=True)

    def _node_ips(self, node_filter=None):
        cluster_configs = self.config.get_val("CLUSTER_CONFIGS") or {}
        result = {}
        for node_key in self.state.bot_state:
            if node_filter is not None and node_key != node_filter:
                continue
            ip = _get_ip(cluster_configs, node_key)
            if ip:
                result[node_key] = ip
        return result

    def lock(self, user_id, command):
        """
        Lock nodes
        """
        parse_ok, error_reply, node_keys, duration = self.parse_command(user_id, "lock", command, True)
        if not parse_ok:
            return error_reply

        max_dur = self.config.get_val("MAX_LOCK_DURATION")
        with self._lock:
            nodes = [self.state.bot_state[node_key] for node_key in node_keys]
            if not all(
                node["status"] == "idle"
                or (find_user_info(node["current_users"], user_id) and node["status"] == "exclusive")
                for node in nodes
            ):
                return self.show_error(user_id, self._msg_with_usage("error.node_in_use_or_shared"))

            timestamp = int(time.time())

            if max_dur > 0:
                for node in nodes:
                    total_duration = duration
                    user_info = find_user_info(node["current_users"], user_id)
                    if user_info:
                        total_duration += user_info["duration"]
                        start_time = user_info["start_time"]
                    else:
                        start_time = timestamp
                    if remaining_duration(start_time, total_duration) > max_dur:
                        return self.show_error(
                            user_id,
                            t(
                                "error.lock_max_duration_exceeded",
                                config=self.config,
                                max_duration=format_duration(max_dur, config=self.config),
                            ),
                        )

            for node in nodes:
                node["status"] = "exclusive"

                total_duration = duration
                user_info = find_user_info(node["current_users"], user_id)
                if not user_info:
                    user_info = create_user_info(user_id, total_duration, timestamp, config=self.config)
                else:
                    total_duration += user_info["duration"]

                user_info["duration"] = total_duration
                user_info["is_notified"] = False
                node["current_users"] = [user_info]

            reply = self.adapter.build_reply(self._msg_with_usage("success.resource_locked", node_key=node_keys), [user_id])
            log_to_file(user_id, "lock", node_keys, duration, config=self.config)
            self._save_and_notify()
            return reply

    def slock(self, user_id, command):
        """
        Share lock nodes
        """
        parse_ok, error_reply, node_keys, duration = self.parse_command(user_id, "slock", command, True)
        if not parse_ok:
            return error_reply

        max_dur = self.config.get_val("MAX_LOCK_DURATION")
        with self._lock:
            nodes = [self.state.bot_state[node_key] for node_key in node_keys]
            if not all(node["status"] != "exclusive" for node in nodes):
                return self.show_error(user_id, self._msg_with_usage("error.node_exclusive_mode"))

            timestamp = int(time.time())

            if max_dur > 0:
                for node in nodes:
                    user_info = find_user_info(node["current_users"], user_id)
                    if user_info:
                        total_duration = user_info["duration"] + duration
                        start_time = user_info["start_time"]
                    else:
                        total_duration = duration
                        start_time = timestamp
                    if remaining_duration(start_time, total_duration) > max_dur:
                        msg = t(
                            "error.slock_max_duration_exceeded",
                            config=self.config,
                            max_duration=format_duration(max_dur, config=self.config),
                        )
                        return self.show_error(user_id, msg)

            for node in nodes:
                node["status"] = "shared"
                user_info = find_user_info(node["current_users"], user_id)
                if not user_info:
                    user_info = create_user_info(user_id, duration, timestamp, config=self.config)
                    node["current_users"].append(user_info)
                else:
                    user_info["duration"] += duration
                    user_info["is_notified"] = False

            reply = self.adapter.build_reply(self._msg_with_usage("success.resource_locked", node_key=node_keys), [user_id])
            log_to_file(user_id, "slock", node_keys, duration, config=self.config)
            self._save_and_notify()
            return reply

    def unlock(self, user_id, command):
        """
        Unlock nodes
        """
        if re.match(r"^\s*(unlock|free)\s*$", command):
            with self._lock:
                for _, node in self.state.bot_state.items():
                    remove_user_info(node["booking_list"], user_id)
                    if node["status"] != "idle":
                        remove_user_info(node["current_users"], user_id)
                        if len(node["current_users"]) == 0:
                            node["status"] = "idle"
                reply = self.adapter.build_reply(
                    self._msg_with_usage("success.resource_released"),
                    [user_id],
                )
                log_to_file(user_id, "unlock", "all", config=self.config)
                self._save_and_notify()
                return reply

        parse_ok, error_reply, node_keys, _ = self.parse_command(user_id, "unlock|free", command)
        if not parse_ok:
            return error_reply

        with self._lock:
            nodes = [self.state.bot_state[node_key] for node_key in node_keys]
            if not all(
                find_user_info(node["current_users"], user_id) or find_user_info(node["booking_list"], user_id)
                for node in nodes
            ):
                return self.show_error(user_id, self._msg_with_usage("error.node_not_requested"))
            for node in nodes:
                remove_user_info(node["current_users"], user_id)
                remove_user_info(node["booking_list"], user_id)
                if len(node["current_users"]) == 0:
                    node["status"] = "idle"
            reply = self.adapter.build_reply(
                self._msg_with_usage("success.resource_released", node_key=node_keys),
                [user_id],
            )
            log_to_file(user_id, "unlock", node_keys, config=self.config)
            self._save_and_notify()
            return reply

    def kickout(self, user_id, command):
        """
        Kickout nodes
        """
        parse_ok, error_reply, node_keys, _ = self.parse_command(user_id, "kickout", command)
        if not parse_ok:
            return error_reply

        with self._lock:
            nodes = [self.state.bot_state[node_key] for node_key in node_keys]
            users = set([user_id])
            content = t("success.resource_force_released", config=self.config, user_id=user_id)
            content += self._msg_with_usage("label.before_release", node_key=node_keys)
            for node in nodes:
                for user_info in node["current_users"]:
                    users.add(user_info["user_id"])
                for user_info in node["booking_list"]:
                    users.add(user_info["user_id"])
                node["status"] = "idle"
                node["current_users"] = []
                node["booking_list"] = []
            content += self._msg_with_usage("label.after_release", node_key=node_keys)
            reply = self.adapter.build_reply(content, list(users))
            log_to_file(user_id, "kickout", node_keys, config=self.config)
            self._save_and_notify()
            return reply

    def _help_commands(self):
        """Return NodeBot-specific command section for help text."""
        cluster_configs = self.config.get_val("CLUSTER_CONFIGS")
        assert len(cluster_configs) >= 1
        itr = iter(cluster_configs)
        example_node0 = next(itr)
        if len(cluster_configs) <= 1:
            example_node1 = None
        else:
            example_node1 = next(itr)

        reply_info = t("help.rule3_lock_modes", config=self.config)
        reply_info += f"    lock {example_node0}\n"
        reply_info += f"    lock {example_node0} 3d\n"
        if example_node1 is not None:
            reply_info += f"    lock {example_node0},{example_node1} 2h\n"
        reply_info += f"    slock {example_node0} 30m\n"
        reply_info += t("help.section2_title", config=self.config)
        reply_info += f"    unlock {example_node0}\n"
        if example_node1 is not None:
            reply_info += f"    free {example_node0},{example_node1} \n"
        reply_info += t("help.free_all", config=self.config)
        reply_info += t("help.section3_title", config=self.config)
        reply_info += f"    kickout {example_node0} \n"
        if example_node1 is not None:
            reply_info += f"    kickout {example_node0},{example_node1} \n"
        reply_info += t("help.section4_title", config=self.config)
        reply_info += t("help.section5_title", config=self.config)
        reply_info += t("help.query_at_bot", config=self.config)
        reply_info += f"    {example_node0}\n\n"
        return reply_info

    def _check_and_notify(self) -> float | None:
        """
        Check resource expiration, release expired resources, and send notifications.
        Persists state only when changes occur.

        Returns: seconds until next interesting event, or None if no active locks.
        """
        EARLY_NOTIFY = self.config.get_val("EARLY_NOTIFY")
        TIME_ALERT = self.config.get_val("TIME_ALERT")

        trigger_time_alert = False
        state_changed = False
        user_ids = set()
        alert_info = self._build_alert_header()

        with self._lock:
            # Release expired resources
            for node_key, node in self.state.bot_state.items():
                if node["status"] != "idle":
                    removed_users_id = []
                    for user_info in node["current_users"]:
                        remaining_time = remaining_duration(user_info["start_time"], user_info["duration"])
                        if remaining_time <= 0:
                            removed_users_id.append(user_info["user_id"])
                            state_changed = True

                            # Send expiry notification only if early warning was never sent.
                            # When EARLY_NOTIFY=True and warning fired on time, is_notified=True → silent release.
                            # When EARLY_NOTIFY=False, is_notified is always False → always notify here.
                            # Fallback: EARLY_NOTIFY=True but scheduler delayed past expiry → notify here instead.
                            if not user_info["is_notified"]:
                                trigger_time_alert = True
                                user_ids.add(user_info["user_id"])

                                uid = user_info["user_id"] + format_access_mode(node["status"], config=self.config)
                                duration = format_duration(remaining_time, config=self.config)
                                alert_info += f"{node_key} {uid}  {duration}\n"

                        if EARLY_NOTIFY and not user_info["is_notified"] and 0 < remaining_time <= TIME_ALERT:
                            trigger_time_alert = True
                            user_ids.add(user_info["user_id"])
                            user_info["is_notified"] = True
                            state_changed = True

                            uid = user_info["user_id"] + format_access_mode(node["status"], config=self.config)
                            duration = format_duration(remaining_time, config=self.config)
                            alert_info += f"{node_key} {uid}  {duration}\n"

                    for user_id in removed_users_id:
                        remove_user_info(node["current_users"], user_id)

                    if len(node["current_users"]) == 0:
                        node["status"] = "idle"

            if state_changed:
                save_bot_state_to_file(self.state.bot_state, config=self.config)

            # Compute next wakeup: scan remaining active users after mutations
            min_next = float("inf")
            for node in self.state.bot_state.values():
                if node["status"] != "idle":
                    for user_info in node["current_users"]:
                        remaining = remaining_duration(user_info["start_time"], user_info["duration"])
                        if remaining <= 0:
                            continue
                        if EARLY_NOTIFY and not user_info["is_notified"]:
                            next_event = remaining - TIME_ALERT
                        else:
                            next_event = remaining
                        min_next = min(min_next, next_event)

        if trigger_time_alert:
            msg = self.adapter.build_reply(alert_info + "\n", list(user_ids))
            try:
                self.adapter.send(msg)
            except Exception:
                self.logger.exception("Failed to send alert for bot %s", self.config.get_val("BOT_NAME"))

        return max(1.0, min_next) if min_next != float("inf") else None

    def _idle_summary(self, node_filter=None):
        idle_nodes = sum(
            1
            for node_key, node_status in self.state.bot_state.items()
            if (node_filter is None or node_key == node_filter) and node_status["status"] == "idle"
        )
        return t("query.idle_summary_node", config=self.config, idle_nodes=idle_nodes)

    def _current_usage(self, node_filter=None, user_id=None):
        """Render NODE usage honoring USAGE_* layout config."""
        line_tpl = self.config.get_val("USAGE_LINE_TEMPLATE")
        idle_tpl = self.config.get_val("USAGE_IDLE_TEMPLATE")
        sort_mode = self.config.get_val("USAGE_SORT")
        group_mode = self.config.get_val("USAGE_GROUP")
        bot_name = self.config.get_val("BOT_NAME")
        fb_line = DEFAULT_LINE_TEMPLATE
        fb_idle = DEFAULT_IDLE_TEMPLATE

        entries = []
        order = 0
        for node_key, node_status in self.state.bot_state.items():
            if not (
                node_filter is None
                or node_key == node_filter
                or (isinstance(node_filter, list) and node_key in node_filter)
            ):
                continue
            rem = min_remaining(node_status)
            rows = []
            if node_status["status"] == "idle":
                rows.append(
                    (
                        True,
                        {
                            "node": "",
                            "dev": "",
                            "model": "",
                            "user": "",
                            "mode": "",
                            "dur": "",
                            "status": t("status.idle", config=self.config),
                        },
                    )
                )
            else:
                for user_info in node_status["current_users"]:
                    duration = format_duration(
                        remaining_duration(user_info["start_time"], user_info["duration"]), config=self.config
                    )
                    rows.append(
                        (
                            False,
                            {
                                "node": "",
                                "dev": "",
                                "model": "",
                                "user": user_info["user_id"],
                                "mode": format_access_mode(node_status["status"], config=self.config),
                                "dur": duration,
                                "status": "",
                            },
                        )
                    )
            entries.append(
                {
                    "order_index": order,
                    "is_idle": rem is None,
                    "is_mine": user_id is not None
                    and any(user_info["user_id"] == user_id for user_info in node_status.get("current_users", [])),
                    "min_remaining": rem,
                    "node_key": node_key,
                    "rows": rows,
                }
            )
            order += 1

        ordered = sort_and_group(entries, sort_mode, group_mode)

        def render_entries(entries_to_render):
            text = ""
            for entry in entries_to_render:
                node_key = entry["node_key"]
                first = True
                for is_idle, fields in entry["rows"]:
                    fields = dict(fields)
                    fields["node"] = node_key if first else " " * len(node_key)
                    tpl, fb = (idle_tpl, fb_idle) if is_idle else (line_tpl, fb_line)
                    text += render_line(tpl, fields, fb, bot_name=bot_name).rstrip() + "\n"
                    first = False
            return text

        my_entries = [entry for entry in ordered if entry["is_mine"]]
        rest_entries = [entry for entry in ordered if not entry["is_mine"]]

        usage_info = ""
        if my_entries:
            usage_info += t("query.my_resources_header", config=self.config)
            usage_info += render_entries(my_entries)
        usage_info += render_entries(rest_entries)
        return usage_info
