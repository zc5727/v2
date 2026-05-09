from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import json

from atm_ops_agent.aliyun_openapi import AliyunRPCClient
from atm_ops_agent.config import CloudMonitorConfig, Server


@dataclass(slots=True)
class MetricPoint:
    metric: str
    value: float
    timestamp: datetime
    unit: str = ""


@dataclass(slots=True)
class MetricSnapshot:
    server: str
    region: str
    points: list[MetricPoint]

    def as_dict(self) -> dict[str, float]:
        return {point.metric: point.value for point in self.points}


DEFAULT_METRICS: dict[str, tuple[str, str]] = {
    "cpu_utilization": ("CPUUtilization", "%"),
    "memory_utilization": ("memory_usedutilization", "%"),
    "intranet_in_rate": ("IntranetRX", "bytes/s"),
    "intranet_out_rate": ("IntranetTX", "bytes/s"),
}


class CloudMonitorService:
    def __init__(self, config: CloudMonitorConfig) -> None:
        self.config = config

    def _require_client(self) -> AliyunRPCClient:
        if not self.config.enabled:
            raise ValueError("cloud monitor is disabled in inventory config")
        if not self.config.access_key_id or not self.config.access_key_secret:
            raise ValueError("missing ALIYUN_ACCESS_KEY_ID or ALIYUN_ACCESS_KEY_SECRET")
        return AliyunRPCClient(
            endpoint=self.config.endpoint,
            access_key_id=self.config.access_key_id,
            access_key_secret=self.config.access_key_secret,
        )

    def _fetch_metric(
        self,
        client: AliyunRPCClient,
        server: Server,
        metric_label: str,
        metric_name: str,
        unit: str,
    ) -> MetricPoint | None:
        if not server.instance_id:
            return None
        region = server.region or self.config.default_region
        if not region:
            raise ValueError(f"server {server.name} has no region configured")
        end_time = datetime.now(UTC)
        start_time = end_time - timedelta(minutes=self.config.lookback_minutes)
        response = client.call(
            action="DescribeMetricList",
            version="2019-01-01",
            extra_params={
                "Namespace": self.config.namespace,
                "MetricName": metric_name,
                "Period": self.config.period,
                "Dimensions": json.dumps([{"instanceId": server.instance_id}]),
                "StartTime": start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "EndTime": end_time.strftime("%Y-%m-%d %H:%M:%S"),
            },
            timeout=20,
        )
        datapoints_raw = response.get("Datapoints", "[]")
        datapoints = json.loads(datapoints_raw) if isinstance(datapoints_raw, str) else datapoints_raw
        if not datapoints:
            return None
        latest = sorted(datapoints, key=lambda item: item.get("timestamp", 0))[-1]
        value = latest.get("Average")
        if value is None:
            for key in ("Value", "value", "Maximum", "minimum"):
                if latest.get(key) is not None:
                    value = latest[key]
                    break
        if value is None:
            return None
        timestamp_ms = int(latest.get("timestamp", 0))
        timestamp = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC) if timestamp_ms else end_time
        return MetricPoint(metric=metric_label, value=float(value), timestamp=timestamp, unit=unit)

    def latest_snapshot(self, server: Server) -> MetricSnapshot:
        client = self._require_client()
        region = server.region or self.config.default_region or "unknown"
        points: list[MetricPoint] = []
        for metric_label, (metric_name, unit) in DEFAULT_METRICS.items():
            point = self._fetch_metric(client, server, metric_label, metric_name, unit)
            if point:
                points.append(point)
        return MetricSnapshot(server=server.name, region=region, points=points)
