"""@brief 环境登记失败的稳定错误码与唯一英文原因。"""

from worldmock.errors import IntegrityError


REGISTRATION_REASONS = {
    "RULES_VERSION_MISMATCH": "The environment was published for a different rules version.",
    "ENVIRONMENT_STALE_FILE": "The environment contains an entry outside its declared package.",
    "ENVIRONMENT_LINE_INVALID": "A catalog JSONL line is not exactly one JSON object.",
    "ENVIRONMENT_FILE_UNLISTED": "The manifest does not list a catalog file that is required or present.",
    "ENVIRONMENT_IDENTITY_MISMATCH": "A catalog file does not match the identity recorded in the manifest.",
    "ENVIRONMENT_ID_DUPLICATE": "An identifier or display name is declared twice in one catalog file.",
    "ENVIRONMENT_REF_UNKNOWN": "A binding refers to an object that is not registered.",
    "ENVIRONMENT_KIND_INVALID": "A declared kind is outside the fixed set.",
    "ENVIRONMENT_NUMBER_INVALID": "A declared number is outside its allowed domain.",
    "HOME_LOCATION_REQUIRED": "The environment must register the persona home as a home location.",
    "HOME_NOT_SLEEPABLE": "The persona home must be marked sleepable.",
    "PARCEL_STATION_REQUIRED": "Parcel stations must exist when required and have location content.",
    "GOODS_BINDING_MISSING": "A goods item must be bound to at least one store kind or content app.",
    "GOODS_BINDING_SIDE_EXCLUSIVE": "A goods item must be bound either to store kinds or apps, not both.",
    "GOODS_BINDING_DANGLING": "A goods item is bound to a kind that no registered location declares.",
    "APP_ROLE_BINDING_INVALID":
        "Only function apps may bind actions, and only content apps may be referenced by locations, goods or contents.",
    "APP_UNBOUND": "An app must have content or a concrete action binding for its role.",
    "APP_NAME_COLLIDES_LOCATION": "An app name must differ from every place name.",
    "CONTENT_CONTAINER_MISSING": "A content must be bound to at least one app or at least one location kind.",
    "CONTENT_CONTAINER_SIDE_EXCLUSIVE": "A content must be bound either to apps or to location kinds, not both.",
    "CONTENT_BINDING_DANGLING": "A content names a location kind that no registered location declares.",
    "CONTENT_PREREQUISITE_INVALID":
        "A content prerequisite must belong to an app content and declare location kinds, opening hours, or both.",
    "CONTENT_SERIES_INVALID":
        "Episodes of a series must share one container binding and one prerequisite and have unique episode numbers.",
    "CONTENT_SUMMARY_TOO_LONG": "A content summary exceeds the registration limit.",
    "CONTENT_SOURCE_URL_MISSING": "A real summary must record its source url.",
    "CALLBACK_STATE_UNREGISTERED": "A callback rule refers to a state that the environment does not register.",
    "CALLBACK_FOOTPRINT_EMPTY": "A callback footprint chain needs at least one step and every step at least one app.",
    "CALLBACK_NOTIFY_MISMATCH":
        "A callback reminder needs a state delta effect and a visible hint needs a footprint chain effect.",
    "MAP_TOO_SMALL": "The map needs at least two places.",
    "MAP_EDGE_INVALID": "The map has an edge that is not a sorted positive link between two registered places.",
    "MAP_DISCONNECTED": "The map is not connected.",
    "MAP_DISTANCE_CONTRADICTED": "A declared distance is longer than a route through other declared distances.",
    "MAP_ROUTE_TOO_LONG":
        "Two places are farther apart than the slowest travel method can cover in one day in the worst weather.",
}


class RegistrationError(IntegrityError):
    """@brief 携带稳定登记码并记录英文故障的完整性错误。"""

    def __init__(self, code: str, detail: str | None = None):
        """@brief 绑定唯一错误原因与可选诊断详情。"""
        self.code = code
        self.message = REGISTRATION_REASONS[code]
        self.detail = detail
        super().__init__(f"{code}: {self.message}" + (f" {detail}" if detail else ""))
