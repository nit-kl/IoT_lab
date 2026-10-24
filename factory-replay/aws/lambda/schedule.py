"""replayer を 1 分ごとに起動する EventBridge Scheduler のスケジュールを有効化・無効化する。"""

import os

import boto3

_client = None

# UpdateSchedule は全項目の置き換えなので、GetSchedule の結果から引き継ぐ項目
_KEEP = ["Name", "GroupName", "ScheduleExpression", "ScheduleExpressionTimezone", "FlexibleTimeWindow",
         "Target", "Description", "StartDate", "EndDate", "KmsKeyArn", "ActionAfterCompletion"]


def set_enabled(enabled, name=None):
    """スケジュールの状態を変える。すでにその状態なら何もしない。変えたら True。"""
    global _client
    _client = _client or boto3.client("scheduler")
    name = name or os.environ["SCHEDULE_NAME"]
    current = _client.get_schedule(Name=name)
    state = "ENABLED" if enabled else "DISABLED"
    if current["State"] == state:
        return False
    request = {k: current[k] for k in _KEEP if k in current}
    _client.update_schedule(**request, State=state)
    return True
