"""观察逐字节往返的唯一核对：记下 observe() 构建的每份观察，核对存储层解码后读回的观察与之相同、存下的正文带分件引用。"""

import copy
import json
from datetime import datetime
from zoneinfo import ZoneInfo

from worldmock.footprints.models import canonical_json, is_sha256
from worldmock.footprints.store import FootprintStore
from worldmock.life.continuation import CONTINUATION_KEY
from worldmock.life.engine import LifeEngine
from worldmock.life.observe import ObservationBuilder

#: 整表各存为一个观察分件的六个观察键（逐字写出，不从生产模块导入）。
WHOLE_KEYS = ("catalog_bindings", "errand_item_bindings", "known_activity_actions", "locations", "memory_layer",
              "persona_views")
#: 只给宿主、不写入观察事件的两个键。
HOST_ONLY_KEYS = ("progress_layer", "progress_meta")


def record_observations(monkeypatch) -> list[tuple[str, dict]]:
    """包住 ObservationBuilder._observation_data 与 LifeEngine.observe（类属性）：每次构建记下一份深拷贝；observe 一返回
    就断言返回的观察（去掉 observation_event_id 与同秒续接）等于刚构建的那份，证明构建到返回之间没有改动它。

    :returns: 逐次追加的 (观察事件身份, 构建时的观察深拷贝) 列表。
    """
    built: list[dict] = []
    recorded: list[tuple[str, dict]] = []
    build, observe = ObservationBuilder._observation_data, LifeEngine.observe

    def recording_build(self, agent):
        data = build(self, agent)
        built.append(copy.deepcopy(data))
        return data

    def recording_observe(self, context):
        returned = observe(self, context)
        assert {key: value for key, value in returned.items()
                if key not in ("observation_event_id", CONTINUATION_KEY)} == built[-1]
        recorded.append((returned["observation_event_id"], built[-1]))
        return returned

    monkeypatch.setattr(ObservationBuilder, "_observation_data", recording_build)
    monkeypatch.setattr(LifeEngine, "observe", recording_observe)
    return recorded


def assert_observations_round_trip(store: FootprintStore, recorded: list[tuple[str, dict]], started: dict[str, int],
                                   zone: str) -> None:
    """运行库里的每条观察都由 recorded 覆盖，按次序；解码读回的观察与构建时（去掉只给宿主的键）规范 JSON 逐字节相同；
    存下的正文在七个分件键上带 sha256；activity_bindings 的引用数等于所指活动开始时刻的场景本地日期数（独立于
    readable.local_date 计算，按 UTC 日期或观察时刻分段都会被发现）。

    :param started: 每项活动身份 → started_at。
    :param zone: 运行的场景时区。
    """
    stored = [event.event_id for event in store.read_events(store.last_event_seq) if event.event_type == "observation"]
    assert stored == [identity for identity, _ in recorded]
    for identity, built in recorded:
        expected = {key: value for key, value in built.items() if key not in HOST_ONLY_KEYS}
        assert canonical_json(store.read_event(identity).data) == canonical_json(expected)
        row = store.connection.execute("SELECT body FROM events WHERE event_id=?", (identity,)).fetchone()
        raw = json.loads(row[0])["data"]
        assert isinstance(raw["activity_bindings"], list)
        assert all(is_sha256(ref) for ref in raw["activity_bindings"])
        assert all(is_sha256(raw[key]) for key in WHOLE_KEYS)
        dates = {datetime.fromtimestamp(started[item["activity_id"]], ZoneInfo(zone)).date()
                 for item in built["activity_bindings"]}
        assert len(raw["activity_bindings"]) == len(dates)
