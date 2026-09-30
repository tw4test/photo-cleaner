from enum import Enum

class MediaType(str, Enum):
    PHOTO = "photo"
    RAW = "raw"
    VIDEO = "video"

class AssetStatus(str, Enum):
    UNANALYSED = "unanalysed"
    ANALYSED = "analysed"
    CANDIDATE = "candidate"
    QUEUED = "queued"
    DELETED = "deleted"
    KEPT = "kept"

class FileRole(str, Enum):
    PRIMARY = "primary"
    PAIR = "pair"

class CandidateRule(str, Enum):
    EXACT_DUP = "exact_dup"
    NEAR_DUP = "near_dup"
    BURST = "burst"
    BLUR = "blur"
    OVER_EXPOSURE = "over_exposure"
    UNDER_EXPOSURE = "under_exposure"
    SCREENSHOT = "screenshot"
    DOCUMENT = "document"
    MEME = "meme"
    MISTOUCH = "mistouch"
    VIDEO_SAME_SIZE = "video_same_size"
    VIDEO_TINY = "video_tiny"

class CandidateState(str, Enum):
    CANDIDATE = "candidate"
    KEPT = "kept"
    QUEUED = "queued"

class GroupType(str, Enum):
    EXACT = "exact"
    NEAR = "near"
    BURST = "burst"
    VIDEO_SIZE = "video_size"

class ActionType(str, Enum):
    DELETE = "delete"
    RESTORE = "restore"

class DeletionMode(str, Enum):
    TRASH = "trash"
    QUARANTINE = "quarantine"
    PERMANENT = "permanent"

class DecodeStatus(str, Enum):
    OK = "ok"
    FALLBACK = "fallback"
    ERROR = "error"   # 解碼失敗（用於失敗記錄）

class HashState(str, Enum):
    PENDING = "pending"
    DONE = "done"
    NOT_NEEDED = "not_needed"
    ERROR = "error"

class AnalysisStage(str, Enum):
    HASH = "hash"
    BASIC = "basic"
    CLIP = "clip"
    VIDEO = "video"

class ComputeDevice(str, Enum):
    AUTO = "auto"
    CUDA = "cuda"
    CPU = "cpu"
