from datetime import datetime
from sqlalchemy import (
    Column, Integer, BigInteger, String, Float, Boolean, DateTime,
    ForeignKey, Text, LargeBinary, JSON
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Root(Base):
    __tablename__ = "roots"
    id = Column(Integer, primary_key=True, autoincrement=True)
    path = Column(String, nullable=False, unique=True)
    recursive = Column(Boolean, default=True, nullable=False)
    exclude_patterns = Column(String, default="", nullable=False)
    priority = Column(Integer, default=0, nullable=False)
    last_scan_at = Column(DateTime, nullable=True)
    available = Column(Boolean, default=True, nullable=False)
    assets = relationship("Asset", back_populates="root", cascade="all, delete-orphan")


class Asset(Base):
    __tablename__ = "assets"
    id = Column(Integer, primary_key=True, autoincrement=True)
    root_id = Column(Integer, ForeignKey("roots.id", ondelete="CASCADE"), nullable=False, index=True)
    media_type = Column(String, nullable=False)
    primary_path = Column(String, nullable=False, unique=True, index=True)
    size_total = Column(BigInteger, default=0, nullable=False)
    paired_paths = Column(JSON, default=list, nullable=False)
    status = Column(String, default="unanalysed", nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    root = relationship("Root", back_populates="assets")
    files = relationship("File", back_populates="asset", cascade="all, delete-orphan")
    features = relationship("Feature", back_populates="asset", uselist=False, cascade="all, delete-orphan")
    clip_feature = relationship("ClipFeature", back_populates="asset", uselist=False, cascade="all, delete-orphan")
    video_info = relationship("VideoInfo", back_populates="asset", uselist=False, cascade="all, delete-orphan")
    candidates = relationship("Candidate", back_populates="asset", cascade="all, delete-orphan")
    actions = relationship("ActionLog", back_populates="asset")
    analysis_failures = relationship("AnalysisFailure", back_populates="asset", cascade="all, delete-orphan")


class File(Base):
    __tablename__ = "files"
    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    path = Column(String, nullable=False, unique=True, index=True)
    size = Column(BigInteger, nullable=False, index=True)
    mtime = Column(Float, nullable=False)
    sha256 = Column(String(64), nullable=True, index=True)
    format = Column(String(20), nullable=False)
    role = Column(String(20), default="primary", nullable=False)
    hash_state = Column(String(20), default="pending", nullable=False)
    asset = relationship("Asset", back_populates="files")


class Feature(Base):
    __tablename__ = "features"
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    phash = Column(String(64), nullable=True, index=True)
    blur_score = Column(Float, nullable=True, index=True)
    brightness = Column(Float, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    taken_at = Column(DateTime, nullable=True, index=True)
    exif = Column(JSON, default=dict, nullable=False)
    decode_status = Column(String(20), default="ok", nullable=False)
    analyzed_at = Column(DateTime, nullable=True)
    asset = relationship("Asset", back_populates="features")


class ClipFeature(Base):
    __tablename__ = "clip_features"
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    model = Column(String(100), nullable=False)
    pretrained = Column(String(100), nullable=False)
    dim = Column(Integer, nullable=False)
    embedding = Column(LargeBinary, nullable=True)
    zs = Column(JSON, default=dict, nullable=False)
    analyzed_at = Column(DateTime, nullable=True)
    asset = relationship("Asset", back_populates="clip_feature")


class VideoInfo(Base):
    __tablename__ = "video_info"
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), primary_key=True)
    duration = Column(Float, nullable=True)
    width = Column(Integer, nullable=True)
    height = Column(Integer, nullable=True)
    codec = Column(String(50), nullable=True)
    asset = relationship("Asset", back_populates="video_info")


class AnalysisFailure(Base):
    __tablename__ = "analysis_failures"
    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    stage = Column(String(20), nullable=False, index=True)
    error_msg = Column(Text, nullable=True)
    occurred_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    asset = relationship("Asset", back_populates="analysis_failures")


class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True, autoincrement=True)
    type = Column(String(30), nullable=False)
    keeper_asset_id = Column(Integer, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    candidates = relationship("Candidate", back_populates="group")
    keeper_asset = relationship("Asset", foreign_keys=[keeper_asset_id])


class Candidate(Base):
    __tablename__ = "candidates"
    id = Column(Integer, primary_key=True, autoincrement=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    rule = Column(String(50), nullable=False, index=True)
    reason = Column(String(255), nullable=False)
    score = Column(Float, default=0.0, nullable=False)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="SET NULL"), nullable=True, index=True)
    is_keeper = Column(Boolean, default=False, nullable=False)
    state = Column(String(20), default="candidate", nullable=False, index=True)
    asset = relationship("Asset", back_populates="candidates")
    group = relationship("Group", back_populates="candidates")


class ActionLog(Base):
    __tablename__ = "actions_log"
    id = Column(Integer, primary_key=True, autoincrement=True)
    batch_id = Column(String(64), nullable=False, index=True)
    asset_id = Column(Integer, ForeignKey("assets.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(20), nullable=False)
    mode = Column(String(20), nullable=False)
    original_path = Column(String, nullable=False)
    new_path = Column(String, nullable=True)
    at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    asset = relationship("Asset", back_populates="actions")


class Setting(Base):
    __tablename__ = "settings"
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)


class AppState(Base):
    """系統內部狀態（不屬於使用者設定，不會被「恢復預設值」清掉），例如上次評估的指紋與結果。"""
    __tablename__ = "app_state"
    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=False)
