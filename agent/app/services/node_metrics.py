"""Bounded host telemetry. Percentages are absent when capacity is unknown."""
import json
import time
import psutil

_previous = None


def network_sample():
    global _previous
    now = time.monotonic()
    counters = psutil.net_io_counters(pernic=True)
    stats = psutil.net_if_stats()
    result = []
    previous = _previous
    _previous = (now, counters)
    for name, value in counters.items():
        if name == 'lo' or name not in stats or not stats[name].isup:
            continue
        rx = tx = utilization = None
        if previous and name in previous[1] and now > previous[0]:
            old = previous[1][name]
            elapsed = now - previous[0]
            rx = max(0, value.bytes_recv - old.bytes_recv) / elapsed
            tx = max(0, value.bytes_sent - old.bytes_sent) / elapsed
            if stats[name].speed > 0:
                utilization = max(rx, tx) * 8 / (stats[name].speed * 1_000_000) * 100
        result.append({'name': name, 'rx_bytes_per_second': rx, 'tx_bytes_per_second': tx,
                       'utilization_percent': utilization, 'speed_mbps': max(0, stats[name].speed)})
    return result


def cache_storage(text):
    counters = json.loads(text).get('counters', {})
    stores = {}
    for name, item in counters.items():
        parts = name.split('.')
        if len(parts) == 3 and parts[0] in {'SMA', 'SMF', 'MSE'} and parts[1] != 'Transient' and parts[2] in {'g_bytes', 'g_space'}:
            stores.setdefault('.'.join(parts[:2]), {})[parts[2]] = item['value']
    result = []
    for name, values in stores.items():
        if 'g_bytes' not in values or 'g_space' not in values:
            continue
        used, free = values['g_bytes'], values['g_space']
        result.append({'name': name, 'used_bytes': used, 'free_bytes': free,
                       'capacity_bytes': used + free,
                       'used_percent': used / (used + free) * 100 if used + free else None})
    return result
