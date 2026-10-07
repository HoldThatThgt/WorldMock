"""@brief 环境登记失败的稳定错误码和英文原因。"""

from worldmock.errors import IntegrityError


REGISTRATION_REASONS = {
    "RULES_VERSION_MISMATCH": "The environment was published for a different rules version.",
    "ENVIRONMENT_STALE_FILE": "The catalog contains a file that the manifest does not list.",
    "ENVIRONMENT_FILE_UNLISTED": "The manifest does not list a required catalog file.",
    "ENVIRONMENT_IDENTITY_MISMATCH": "A catalog file does not match the identity recorded in the manifest.",
    "ENVIRONMENT_PIN_MISMATCH": "The scenario pins an environment that is not the one being loaded.",
    "ENVIRONMENT_ID_DUPLICATE": "An identifier or display name is declared twice in one catalog file.",
    "ENVIRONMENT_REF_UNKNOWN": "A binding refers to an object that is not registered.",
    "ENVIRONMENT_KIND_INVALID": "A declared kind is outside the fixed set.",
    "ENVIRONMENT_NUMBER_INVALID": "A declared number is outside its allowed domain.",
    "GOODS_BINDING_MISSING": "A goods item must be bound to stores or to all stores that may supply its category.",
    "GOODS_STORE_KIND_INVALID": "A goods item is bound to a place that cannot supply its category.",
    "GOODS_BINDING_KIND_INCONSISTENT": "A goods item must be bound to every store of a kind or to none of them.",
    "APP_PURPOSE_INVALID": "An app purpose does not match its store or payment bindings.",
    "ACTION_APP_MANNER_INVALID": "An action must declare a manner exactly when it is bound to apps.",
    "ACTION_CONTENT_EMPTY": "An action and each of its apps must have at least one content.",
    "ACTION_NAME_COLLIDES":
        "An action name must differ from every other action, app, place, goods and way name.",
    "CONTENT_ACTION_MISSING": "A content must be bound to one registered action.",
    "CONTENT_APP_BINDING_INVALID":
        "A content must list a non-empty subset of its action's apps, or none when the action has no app.",
    "CONTENT_SERIES_INVALID":
        "Episodes of a series must share one action and one app set and have unique episode numbers.",
    "CONTENT_SUMMARY_TOO_LONG": "A content summary exceeds the registration limit.",
    "CONTENT_SOURCE_URL_MISSING": "A real summary must record its source url.",
    "APP_UNBOUND": "An app must declare a purpose or be bound by at least one action.",
    "APP_NAME_COLLIDES_LOCATION": "An app name must differ from every place name.",
    "MAP_TOO_SMALL": "The map needs at least two places.",
    "MAP_EDGE_INVALID": "The map has an edge that is not a sorted positive link between two registered places.",
    "MAP_DISCONNECTED": "The map is not connected.",
    "MAP_NOT_REGENERATED": "The published map differs from the generator output for its seed.",
    "WORK_CONTENT_UNKNOWN": "A scenario assigns a work content that is not a registered work and study content.",
    "MERCHANT_ACCOUNT_MISSING": "A selected store has no merchant account.",
    "WORK_SCHEDULE_REQUIRED": "A person with assigned work content must declare a work schedule.",
    "WORK_CONTENT_REQUIRED": "A person with a work schedule must be assigned at least one work content.",
    "WORK_CONTENT_INSUFFICIENT":
        "The assigned work content is shorter than the person's scheduled shifts in the run window.",
}


class RegistrationError(IntegrityError):
    """@brief 携带稳定登记码并记录英文故障的完整性错误。"""

    def __init__(self, code: str, detail: str | None = None):
        """@brief 绑定唯一定义的错误原因和可选诊断详情。"""
        self.code = code
        self.message = REGISTRATION_REASONS[code]
        self.detail = detail
        super().__init__(f"{code}: {self.message}" + (f" {detail}" if detail else ""))
