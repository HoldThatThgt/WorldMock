"""观察逐字节往返的唯一核对：记下 prepare_barrier() 构建的每份观察，核对存储层解码后读回的观察与之相同、存下的正文带分件引用。"""

import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.foundation.serialization import canonical_json, is_sha256
from worldmock.recording_engine.storage.store import FootprintStore
from worldmock.simulation_engine.engine import LifeEngine
from worldmock.simulation_engine.observation.layers.observe import ObservationBuilder

#: 整表各存为一个观察分件的六个观察键（逐字写出，不从生产模块导入）。
WHOLE_KEYS = ("catalog_bindings", "errand_item_bindings", "known_activity_actions", "locations", "memory_layer",
              "persona_views")
#: 只给宿主、不写入观察事件的两个键。
HOST_ONLY_KEYS = ("progress_layer", "progress_meta")


def record_observations(monkeypatch) -> list[tuple[str, dict]]:
    """包住 ObservationBuilder._observation_data 与 LifeEngine.prepare_barrier（类属性）：每次构建记下一份深拷贝；
    prepare_barrier 返回后断言每份观察（去掉 observation_event_id）等于刚构建的那份，证明构建到返回之间没有改动它。

    :returns: 逐次追加的 (观察事件身份, 构建时的观察深拷贝) 列表。
    """
    built: list[dict] = []
    recorded: list[tuple[str, dict]] = []
    build, prepare_barrier = ObservationBuilder._observation_bundle, LifeEngine.prepare_barrier

    def recording_build(self, agent, state):
        data, stamps = build(self, agent, state)
        built.append(copy.deepcopy(data))
        return data, stamps

    def recording_prepare_barrier(self, *args, **kwargs):
        start = len(built)
        wakeups = prepare_barrier(self, *args, **kwargs)
        for wakeup, built_item in zip(wakeups, built[start:], strict=True):
            assert {key: value for key, value in wakeup.observation.items()
                    if key != "observation_event_id"} == built_item
            recorded.append((wakeup.observation["observation_event_id"], built_item))
        return wakeups

    monkeypatch.setattr(ObservationBuilder, "_observation_bundle", recording_build)
    monkeypatch.setattr(LifeEngine, "prepare_barrier", recording_prepare_barrier)
    return recorded


def assert_observations_round_trip(store: FootprintStore, recorded: list[tuple[str, dict]], started: dict[str, int],
                                   zone: str) -> None:
    """运行库里的每条观察都由 recorded 覆盖，按次序；解码读回的观察与构建时（去掉只给宿主的键）规范 JSON 逐字节相同；
    存下的正文在七个分件键上带 sha256；activity_bindings 的引用数等于所指活动开始时刻的场景本地日期数（独立于
    readable.local_date 计算，按 UTC 日期或观察时刻分段都会被发现）。

    :param started: 每项活动身份 → started_at。
    :param zone: 运行的场景时区。
    """
    stored = [event.event_id for event in list(store.iter_events(store.last_event_seq)) if event.event_type == "observation"]
    assert stored == [identity for identity, _ in recorded]
    for identity, built in recorded:
        expected = {**{key: value for key, value in built.items() if key not in HOST_ONLY_KEYS},
                    "observation_event_id": identity}
        assert canonical_json(store.read_event(identity).data) == canonical_json(expected)
        row = store.connection.execute("SELECT body FROM events WHERE event_id=?", (identity,)).fetchone()
        raw = json.loads(row[0])["data"]
        assert isinstance(raw["activity_bindings"], list)
        assert all(is_sha256(ref) for ref in raw["activity_bindings"])
        assert all(is_sha256(raw[key]) for key in WHOLE_KEYS)
        dates = {datetime.fromtimestamp(started[item["activity_id"]], ZoneInfo(zone)).date()
                 for item in built["activity_bindings"]}
        assert len(raw["activity_bindings"]) == len(dates)
